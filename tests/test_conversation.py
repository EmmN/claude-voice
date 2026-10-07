"""Whole conversations with the listener (listen.Jarvis): a fake microphone (loud/quiet frames), a fake Whisper
(scripted transcripts), the real dispatch in the test environment, and Jarvis's speech and tool calls recorded."""
import json, os, types, unittest.mock as mock
import numpy as np
import helpers
from helpers import VoiceTest
import listen
from listen import FRAME

SAY = [800] * 10 + [0] * 36          # you say something, then 2.9 s of quiet (VOICE_SILENCE is 2.5 s)
NOTHING = [0] * 110                  # silence longer than the 8 s first wait


class Mic:
    def __init__(self, *chunks):
        self.frames = [np.full(FRAME, lv, dtype=np.int16) for c in chunks for lv in c]
    def frame(self): return self.frames.pop(0) if self.frames else None
    def drain(self): pass
    def pause(self): pass
    def resume(self): pass


class TestJarvis(listen.Jarvis):
    """Jarvis with its outside world recorded: what it says, beeps and the tools it would run."""
    def __init__(self, *a, transcripts=(), **kw):
        super().__init__(*a, **kw)
        self.said, self.tools, self.transcripts, self.beeps = [], [], list(transcripts), 0
        self.transcribe = lambda audio, hint: self.transcripts.pop(0) if self.transcripts else ""
    def say_prompt(self, text, cache=True): self.said.append(text)
    def play_interruptible(self, text, cache=True): self.said.append(text); return None
    def beep(self): self.beeps += 1
    def run_tool(self, name, *argv):
        self.tools.append((name, *argv)); return types.SimpleNamespace(returncode=0, stdout=f"{name} ok", stderr="")


class Conversation(VoiceTest):
    def setUp(self):
        super().setUp()
        for p in (mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(listen, "PLATFORM", "linux"),
                  mock.patch.object(listen, "warm_brain", lambda: None), mock.patch.object(listen, "log", lambda *a, **k: None)):
            p.start(); self.addCleanup(p.stop)

    def jarvis(self, *mic, transcripts=(), wake="hey_jarvis", spotter=None):
        args = types.SimpleNamespace(no_ack=False, shared=self.shared, threshold=0.5)
        lock = listen.Lock(self.env["VOICE_LOCK"])
        listen.WAKE_NAME[0] = wake.replace("_", " ")
        oww = mock.Mock(); oww.predict.return_value = {"w": 0.0}
        return TestJarvis(args, Mic(*mic), lock, None, oww=None if spotter else oww, wake_key="w", spotter=spotter,
                          transcripts=transcripts)

    def wake(self, j, **kw):
        j.handle_wake(1.0, **kw); return j.said

    # --- greeting and routing ------------------------------------------------------------------------------------
    def test_several_sessions_pick_one_then_ask(self):
        key = self.add_session("payments"); self.add_session("calendar_sync")
        j = self.jarvis(NOTHING[:15], SAY, SAY, transcripts=["", "payments", "run the tests"])   # silence after the wake word
        said = self.wake(j)
        self.assertEqual(said[0], "Which session: calendar sync or payments?")
        self.assertEqual(said[1], "OK, payments. What do you want me to do?")
        self.assertEqual(said[2], "Sent to payments. It will get it after its next turn.")
        self.assertEqual(self.read(self.shared, "inbox", key + ".msg"), "run the tests\n")

    def test_one_session_is_greeted_by_name(self):
        self.add_session("payments")
        said = self.wake(self.jarvis(NOTHING[:15], SAY, transcripts=["", "check the logs"]))
        self.assertEqual(said[0], "OK, payments. What do you want me to do?")
        self.assertTrue(said[1].startswith("Sent to payments."))

    def test_request_in_the_same_breath_skips_the_greeting(self):
        self.add_session("payments"); self.add_session("calendar_sync")
        said = self.wake(self.jarvis(transcripts=[]), rest="tell payments to check the logs")
        self.assertEqual(len(said), 1); self.assertTrue(said[0].startswith("Sent to payments."))

    def test_unnamed_request_with_several_sessions_asks_which(self):
        self.add_session("payments"); self.add_session("calendar_sync")
        said = self.wake(self.jarvis(SAY, transcripts=["payments"]), rest="run the tests")
        self.assertEqual(said[0], "Which session? Open: calendar sync and payments.")

    def test_nobody_answers(self):
        self.add_session("payments"); self.add_session("calendar_sync")
        said = self.wake(self.jarvis(NOTHING[:15], NOTHING, transcripts=[""]))
        self.assertEqual(said[-1], "I didn't hear anything.")

    def test_no_session_yet(self):
        said = self.wake(self.jarvis(NOTHING[:15], NOTHING, transcripts=[""]))
        self.assertEqual(said[0], "Yes? No session has voice on yet. You can start or resume one.")

    def test_listing_then_choosing(self):
        self.add_session("payments"); self.add_session("calendar_sync")
        said = self.wake(self.jarvis(SAY, SAY, transcripts=["payments", "check the logs"]), rest="list sessions")
        self.assertEqual(said[0], "2 open: calendar sync and payments. Which one?")
        self.assertEqual(said[1], "OK, payments. What do you want me to do?")

    # --- stop / never mind ---------------------------------------------------------------------------------------
    def test_stop_drops_replies(self):
        self.add_session("payments")
        said = self.wake(self.jarvis(), rest="stop")
        self.assertEqual(said, ["OK."])
        self.assertEqual(self.read(self.tmp, "speech.stop"), "stop")

    def test_never_mind_only_ends_the_conversation(self):
        self.add_session("payments")
        self.wake(self.jarvis(), rest="never mind")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "speech.stop")))

    # --- opening, closing, resuming (always confirmed) -----------------------------------------------------------
    def test_open_needs_a_clear_yes(self):
        self.project("webshop")
        j = self.jarvis(SAY, transcripts=["Yes."])
        said = self.wake(j, rest="start a session in webshop called billing")
        self.assertEqual(said[0], "Start a new session called billing in webshop?")
        self.assertEqual(j.tools, [("session-open", f"{self.projects}/webshop", "billing", "")])
        self.assertTrue(said[-1].startswith("Opening billing."))

    def test_open_anything_but_yes_cancels(self):
        self.project("webshop")
        for answer in ("No.", "Yes, no wait.", ""):
            j = self.jarvis(SAY, transcripts=[answer])
            self.wake(j, rest="start a session in webshop")
            self.assertEqual(j.tools, [], answer)

    def test_start_without_a_project_offers_the_most_used(self):
        self.project("webshop")
        self.transcript(f"{self.projects}/webshop", "a1", ai_title="x")
        j = self.jarvis(SAY, SAY, transcripts=["webshop", "yes"])
        said = self.wake(j, rest="start a new session")
        self.assertEqual(said[0], "Which project? Most used: webshop. Or name any other folder.")
        self.assertEqual(j.tools[0][:2], ("session-open", f"{self.projects}/webshop"))

    def test_close(self):
        key = self.add_session("payments")
        j = self.jarvis(SAY, transcripts=["yes"])
        said = self.wake(j, rest="close the payments session")
        self.assertEqual(said[0], "Close the payments session? Anything it is in the middle of stops.")
        self.assertEqual(j.tools, [("session-close", key)])

    def test_resume_from_the_list(self):
        self.project("webshop")
        self.transcript(f"{self.projects}/webshop", "j1", custom_title="flaky_tests", age_days=0.1)
        self.transcript(f"{self.projects}/webshop", "s1", custom_title="webhook_retries", age_days=0.2)
        j = self.jarvis(SAY, SAY, transcripts=["two", "yes"])
        said = self.wake(j, rest="list the old sessions")
        self.assertTrue(said[0].startswith("One, flaky tests, in webshop, today. Two, webhook retries"), said[0])
        self.assertEqual(said[1], "Resume webhook retries, in webshop, from today?")
        self.assertEqual(j.tools[0], ("session-open", f"{self.projects}/webshop", "webhook_retries", "--resume", "s1", "--distro", self.distro))

    def test_an_open_session_is_not_resumed_twice(self):
        self.project("webshop")
        self.transcript(f"{self.projects}/webshop", "j1", custom_title="flaky_tests")
        self.claude_session_file("j1", "flaky_tests")
        j = self.jarvis()
        said = self.wake(j, rest="resume flaky tests")
        self.assertEqual(said, ["flaky tests is already open in its own tab."]); self.assertEqual(j.tools, [])

    # --- the wake word and housekeeping ----------------------------------------------------------------------------
    def test_request_said_with_an_openwakeword_wake_comes_from_the_ring(self):
        self.add_session("payments")
        j = self.jarvis(NOTHING[:15], transcripts=["Hey Jarvis, list sessions"])
        for _ in range(10): j.ring.append(np.full(FRAME, 800, dtype=np.int16))
        said = self.wake(j)                    # the detector fired late: "list sessions" is already in the ring
        self.assertEqual(said[0], "1 open: payments. Which one?")

    def test_listening_mark_while_talking_and_lock_released_after(self):
        self.add_session("payments")
        seen = []
        j = self.jarvis(transcripts=[])
        j.say_prompt = lambda text, cache=True: seen.append(os.path.exists(os.path.join(self.shared, "listening")))
        self.wake(j, rest="never mind")
        self.assertEqual(seen, [True])
        self.assertFalse(os.path.exists(os.path.join(self.shared, "listening")))
        self.assertFalse(j.lock.held_by_someone())

    def test_a_failing_conversation_does_not_take_the_listener_down(self):
        self.add_session("payments")
        j = self.jarvis()
        j.dispatch = mock.Mock(side_effect=RuntimeError("boom"))
        self.wake(j, rest="anything")                            # no exception escapes
        self.assertFalse(os.path.exists(os.path.join(self.shared, "listening")))


class InterruptingAReply(Conversation):
    """"hey Jarvis" while a reply plays: the reply is dropped unless a request is sent to a session."""
    def wake_during_reply(self, j, **kw):
        import fcntl
        reply = open(self.env["VOICE_LOCK"], "a+"); fcntl.flock(reply, fcntl.LOCK_EX)   # a reply is playing
        try: j.handle_wake(1.0, **kw)
        finally: reply.close()
        return self.read(self.tmp, "speech.stop")

    def test_stop_said_after_the_reply_s_own_words_is_found(self):
        self.add_session("payments")
        j = self.jarvis(NOTHING[:15], transcripts=["and the deploy finished cleanly Hey Jarvis stop"])
        for _ in range(10): j.ring.append(np.full(FRAME, 800, dtype=np.int16))
        self.assertEqual(self.wake_during_reply(j), "stop")
        self.assertEqual(j.said, ["OK."])

    def test_never_mind_drops_the_reply(self):
        self.add_session("payments")
        self.assertEqual(self.wake_during_reply(self.jarvis(), rest="never mind"), "stop")

    def test_a_request_keeps_the_reply_for_later(self):
        self.add_session("payments")
        self.assertEqual(self.wake_during_reply(self.jarvis(), rest="tell payments to check the logs"), "wake")


class Robustness(Conversation):
    def test_the_brain_session_answers_unnamed_questions(self):
        self.add_session("webshop_7c"); key = self.add_session("jarvis")
        with open(os.path.join(self.shared, "jarvis_brain"), "w") as f: f.write(key)
        j = self.jarvis()
        self.assertEqual(self.wake(j, rest="what can you do"), ["Let me think about that."])
        self.assertIn("Request for Jarvis", self.read(self.shared, "inbox", key + ".msg"))

    def test_a_reply_that_says_the_wake_word_raises_the_bar(self):
        j = self.jarvis()
        self.assertFalse(j.reply_says_wake_word())
        with open(os.path.join(self.shared, "speaking"), "w") as f: f.write("Hi, I'm Jarvis, your assistant.")
        self.assertTrue(j.reply_says_wake_word())

    def test_prompt_is_not_cut_by_its_own_echo_after_a_slow_player_start(self):
        """The player starts after 8 silent frames; Jarvis's echo (level 900, speech) must not count as you;
        a much louder voice after that does."""
        class Player:
            def __init__(s): s.n = 0
            def poll(s): s.n += 1; return None if s.n < 60 else 0
            def wait(s): return 0
        levels = [0] * 8 + [900] * 30 + [4000] * 10
        j = self.jarvis(levels)
        with mock.patch.object(listen, "prompt_wav", lambda t, c=True: os.path.join(self.tmp, "x.wav")), \
             mock.patch.object(listen.subprocess, "Popen", lambda *a, **k: Player()), \
             mock.patch.object(listen, "speech_prob", lambda f: 0.99):
            got = listen.Jarvis.play_interruptible(j, "Which session: a or b?")
        self.assertIsNotNone(got)                                   # the loud voice interrupted
        self.assertTrue(all(int(f[0]) == 4000 for f in got[-4:]))   # and it was not the echo

    def test_a_stalled_microphone_is_reopened(self):
        import queue, subprocess as sp
        src = listen.Source.__new__(listen.Source)
        src.q, src.paused, src.wav = queue.Queue(), False, False
        src.proc = sp.Popen(["sleep", "30"])
        self.addCleanup(lambda: src.proc.kill() if src.proc.poll() is None else None)
        frame = src.frame()                                         # 2 s without audio
        self.assertEqual(int(np.abs(frame).sum()), 0)
        self.assertIsNotNone(src.proc.wait(timeout=5))              # the stalled capture was ended (the reader reopens it)
