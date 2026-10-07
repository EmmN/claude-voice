"""say and play: queueing on the speech lock, interrupt → replay, "stop" → drop, the listening mark, volume, length.
Piper and the audio player are stand-ins (see VoiceTest.fake_audio); what matters is the order of events."""
import fcntl, os, subprocess, time
from helpers import VoiceTest, BIN


class Speech(VoiceTest):
    def setUp(self):
        super().setUp(); self.fake_audio(clip_seconds=1.5)

    def say(self, text, wait=True, env=None):
        p = subprocess.Popen([os.path.join(BIN, "say"), text], env={**self.env, **(env or {})},
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if wait: p.wait(timeout=30)
        return p

    def hold_lock(self):
        f = open(self.env["VOICE_LOCK"], "a+"); fcntl.flock(f, fcntl.LOCK_EX); return f

    def until(self, cond, timeout=15):
        t = time.time()
        while time.time() - t < timeout:
            if cond(): return True
            time.sleep(0.1)
        return False

    def test_plays_markdown_free_text_at_reply_volume(self):
        self.say("**Hello** there, see `bin/say`.")
        ev = self.audio_events()
        self.assertEqual(ev[0], "piper Hello there, see say.")
        self.assertEqual(ev[1], "filter volume=0.6")
        self.assertTrue(ev[2].startswith("play say-"))
        self.assertEqual(self.speech_log()[-1], "say: played")

    def test_full_volume_skips_the_filter(self):
        self.say("hi", env={"VOICE_REPLY_VOLUME": "1"})
        self.assertFalse([e for e in self.audio_events() if e.startswith("filter")])

    def test_long_text_is_cut(self):
        self.say("word " * 100, env={"SAY_MAX_CHARS": "50"})
        self.assertTrue(self.audio_events()[0].endswith("That is the short version; the rest is on screen."))

    def test_waits_for_the_lock(self):
        lock = self.hold_lock()
        p = self.say("queued", wait=False)
        time.sleep(1.5)
        self.assertFalse([e for e in self.audio_events() if e.startswith("play")])   # nothing while the lock is held
        lock.close(); p.wait(timeout=20)
        self.assertEqual(self.speech_log()[-1], "say: played")

    def test_interrupt_replays_after_the_conversation(self):
        p = self.say("a reply that gets interrupted", wait=False)
        self.assertTrue(self.until(lambda: any(e.startswith("play") for e in self.audio_events())))
        lock_holder = None
        with open(self.stop_file, "w") as f: f.write("wake")       # what the listener does on "hey Jarvis"
        self.assertTrue(self.until(lambda: "say: interrupted (wake); replaying after the conversation" in self.speech_log()))
        lock_holder = self.hold_lock(); time.sleep(1); lock_holder.close()   # the conversation
        p.wait(timeout=20)
        self.assertEqual(len([e for e in self.audio_events() if e.startswith("play")]), 2)   # played again
        self.assertEqual(self.speech_log()[-1], "say: played")

    def test_stop_drops_it(self):
        p = self.say("a reply that gets stopped", wait=False)
        self.assertTrue(self.until(lambda: any(e.startswith("play") for e in self.audio_events())))
        with open(self.stop_file, "w") as f: f.write("wake")
        self.assertTrue(self.until(lambda: any("interrupted" in l for l in self.speech_log())))
        lock = self.hold_lock()
        with open(self.stop_file, "w") as f: f.write("stop")       # "hey Jarvis, stop"
        lock.close(); p.wait(timeout=20)
        self.assertEqual(self.speech_log()[-1], "say: dropped (stop)")
        self.assertEqual(len([e for e in self.audio_events() if e.startswith("play")]), 1)

    def test_voice_stop_drops_queued_replies(self):
        lock = self.hold_lock()
        p = self.say("queued", wait=False); time.sleep(1)
        r = self.run_bin("voice", "stop"); self.assertIn("stopped speaking", r.stdout)
        lock.close(); p.wait(timeout=20)
        self.assertEqual(self.speech_log()[-1], "say: dropped (stop)")

    def test_listening_mark_holds_replies_back(self):
        mark = os.path.join(self.shared, "listening"); open(mark, "w").close()
        p = self.say("waits for the conversation", wait=False)
        time.sleep(2)
        self.assertFalse([e for e in self.audio_events() if e.startswith("play")])
        os.remove(mark); p.wait(timeout=20)
        self.assertEqual(self.speech_log()[-1], "say: played")

    def test_play_plays_a_wav_and_returns(self):
        wav = os.path.join(self.tmp, "short.wav")
        import wave
        w = wave.open(wav, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050); w.writeframes(bytes(2205 * 2)); w.close()
        t = time.time(); self.run_bin("play", wav, check=True)
        self.assertLess(time.time() - t, 8)


class ReplyGap(Speech):
    def test_a_pause_between_two_replies(self):
        self.env["VOICE_REPLY_GAP"] = "2"
        first = self.say("first reply", wait=False); time.sleep(0.3)
        second = self.say("second reply", wait=False)
        first.wait(timeout=30); second.wait(timeout=30)
        log = open(os.path.join(self.voice_home, "speech.log")).read().splitlines()
        stamps = [l.split(" ", 1)[0] for l in log if l.endswith("say: got the lock")]
        played = [l.split(" ", 1)[0] for l in log if l.endswith("say: played")]
        to_s = lambda t: sum(float(x) * m for x, m in zip(t.split(":"), (3600, 60, 1)))
        self.assertGreaterEqual(to_s(stamps[1]) - to_s(played[0]), 1.9)   # the second waited the gap after the first

    def test_jarvis_prompts_are_not_delayed(self):
        self.env["VOICE_REPLY_GAP"] = "5"
        self.say("a reply")
        wav = os.path.join(self.tmp, "p.wav")
        import wave
        w = wave.open(wav, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050); w.writeframes(bytes(2205 * 2)); w.close()
        t = time.time(); self.run_bin("play", wav, check=True)
        self.assertLess(time.time() - t, 2)                     # play (Jarvis's prompts) has no gap


class PlayerStart(VoiceTest):
    def test_the_player_never_inherits_the_speech_lock(self):
        """say starts the Windows player while holding the lock on fd 9; the player must not keep it."""
        fake = os.path.join(self.tmp, "fakebin"); os.makedirs(fake)
        for name, body in (("powershell.exe", "exec sleep 30"), ("wslpath", 'echo "$2"')):
            with open(os.path.join(fake, name), "w") as f: f.write("#!/usr/bin/env bash\n" + body + "\n")
            os.chmod(os.path.join(fake, name), 0o755)
        open(os.path.join(self.shared, "player.ps1"), "w").close()
        env = {**self.env, "PATH": fake + ":" + self.env["PATH"], "CLAUDE_VOICE_PLATFORM": "wsl"}
        lock = self.env["VOICE_LOCK"]
        subprocess.run(["bash", "-c", f'. "{BIN}/lib.sh"; exec 9>"{lock}"; flock 9; start_player'], env=env, timeout=10)
        time.sleep(0.5)
        self.addCleanup(lambda: subprocess.run(["pkill", "-f", "^sleep 30$"]))
        free = subprocess.run(["flock", "-n", lock, "true"]).returncode == 0
        self.assertTrue(free, "the started player holds the speech lock")
