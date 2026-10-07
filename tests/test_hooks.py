"""The Claude Code hooks: /speak, the inbox waiter, the Stop hook's bookkeeping, SessionStart and SessionEnd."""
import json, os, subprocess, time
from helpers import VoiceTest, BIN


class Hooks(VoiceTest):
    SID = "11111111-2222-3333-4444-555555555555"

    def setUp(self):
        super().setUp()
        self.claude_session_file(self.SID, "billing", cwd="/home/u/projects/webshop")
        self.key = f"{self.distro}__{self.SID}"
        self.reg = os.path.join(self.shared, "sessions", self.key + ".json")

    def registry(self):
        with open(self.reg) as f: return json.load(f)

    def hook(self, name, payload, env=None):
        return self.run_bin(name, stdin=json.dumps(payload), env=env)

    def speak(self, arg):
        return self.hook("hook-prompt", {"session_id": self.SID, "prompt": f"/speak {arg}"})

    # --- /speak ------------------------------------------------------------------------------------------------------
    def test_speak_on_registers_focuses_and_tells_both_user_and_model(self):
        out = json.loads(self.speak("on").stdout)
        self.assertIn("voice ON for session billing", out["systemMessage"])
        self.assertTrue(out["hookSpecificOutput"]["additionalContext"].startswith("claude-voice hook result:"))
        self.assertTrue(os.path.exists(os.path.join(self.voice_home, "sessions", self.SID)))
        self.assertEqual(self.registry()["name"], "billing")
        self.assertEqual(self.read(self.voice_home, "focus"), self.key)

    def test_speak_off_unregisters(self):
        self.speak("on"); self.speak("off")
        self.assertFalse(os.path.exists(self.reg))
        self.assertFalse(os.path.exists(os.path.join(self.voice_home, "sessions", self.SID)))

    def test_speak_jarvis_makes_it_the_brain(self):
        self.speak("on"); self.speak("jarvis")
        self.assertEqual(self.read(self.shared, "jarvis_brain"), self.key)
        self.speak("jarvis off")
        self.assertFalse(os.path.exists(os.path.join(self.shared, "jarvis_brain")))

    def test_every_prompt_marks_the_session_busy(self):
        self.speak("on")
        self.hook("hook-prompt", {"session_id": self.SID, "prompt": "run the tests"})
        d = self.registry()
        self.assertEqual((d["status"], d["last_prompt"]), ("busy", "run the tests"))

    # --- inbox waiter ------------------------------------------------------------------------------------------------
    def test_inbox_delivers_with_the_voice_instructions(self):
        self.speak("on")
        with open(os.path.join(self.shared, "inbox", self.key + ".msg"), "w") as f: f.write("check the logs\n")
        r = self.hook("hook-inbox", {"session_id": self.SID})
        self.assertEqual(r.returncode, 2)                       # asyncRewake: exit 2 wakes the session
        self.assertIn("🎤 You said:", r.stderr)
        self.assertIn("read aloud", r.stderr)
        self.assertTrue(r.stderr.rstrip().endswith("check the logs"))
        self.assertFalse(os.path.exists(os.path.join(self.shared, "inbox", self.key + ".msg")))

    def test_inbox_waiter_quits_without_voice(self):
        r = self.hook("hook-inbox", {"session_id": self.SID})
        self.assertEqual(r.returncode, 0)

    def test_newer_waiter_takes_over(self):
        self.speak("on")
        p = subprocess.Popen([os.path.join(BIN, "hook-inbox")], stdin=subprocess.PIPE, env=self.env, text=True)
        p.stdin.write(json.dumps({"session_id": self.SID})); p.stdin.close()
        time.sleep(1.5)
        with open(os.path.join(self.voice_home, "sessions", f".{self.SID}.waiter"), "w") as f: f.write("1")
        self.assertEqual(p.wait(timeout=5), 0)

    # --- Stop hook (muted: bookkeeping only, nothing is played) ------------------------------------------------------
    def test_stop_hook_records_the_reply_without_the_echoed_request(self):
        self.speak("on"); open(os.path.join(self.voice_home, "muted"), "w").close()
        msg = '🎤 You said: "check the logs"\n**All good**: the logs are clean.'
        self.hook("hook-stop", {"session_id": self.SID, "last_assistant_message": msg})
        d = self.registry()
        self.assertEqual(d["status"], "idle")
        self.assertEqual(d["last_reply"], "All good: the logs are clean.")

    # --- SessionStart / SessionEnd -----------------------------------------------------------------------------------
    def test_session_start_turns_voice_on_only_for_sessions_jarvis_opened(self):
        r = self.hook("hook-start", {"session_id": self.SID})
        self.assertEqual(r.stdout, "")
        r = self.hook("hook-start", {"session_id": self.SID}, env={"CLAUDE_VOICE_AUTO": "1"})
        self.assertIn("voice on", json.loads(r.stdout)["systemMessage"])
        for _ in range(40):
            if os.path.exists(self.reg): break
            time.sleep(0.25)
        self.assertTrue(os.path.exists(self.reg))

    def test_session_end_forgets_everything(self):
        self.speak("on"); self.speak("jarvis")
        with open(os.path.join(self.shared, "inbox", self.key + ".msg"), "w") as f: f.write("x\n")
        self.hook("hook-end", {"session_id": self.SID})
        for p in (self.reg, os.path.join(self.shared, "inbox", self.key + ".msg"),
                  os.path.join(self.voice_home, "sessions", self.SID), os.path.join(self.shared, "jarvis_brain")):
            self.assertFalse(os.path.exists(p), p)
