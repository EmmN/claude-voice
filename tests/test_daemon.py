"""listen --daemon / --status / --stop: the supervised background listener, with a stand-in for listen.py."""
import os, subprocess, time
from helpers import VoiceTest


class Daemon(VoiceTest):
    def setUp(self):
        super().setUp()
        venv = os.path.join(self.tmp, "venv"); os.makedirs(os.path.join(venv, "bin"))
        self.starts = os.path.join(self.tmp, "starts")
        # the stand-in "python": records each start, then runs (FAKE_LISTENER=run) or crashes at once (=crash)
        with open(os.path.join(venv, "bin", "python"), "w") as f:
            f.write(f"""#!/usr/bin/env python3
import os, sys, time
open({self.starts!r}, "a").write(str(os.getpid()) + chr(10))
if os.environ.get("FAKE_LISTENER") == "crash": sys.exit(3)
time.sleep(300)
""")
        os.chmod(os.path.join(venv, "bin", "python"), 0o755)
        self.env.update(VOICE_VENV=venv, FAKE_LISTENER="run")
        self.addCleanup(lambda: self.run_bin("listen", "--stop"))

    def starts_count(self):
        try:
            with open(self.starts) as f: return len(f.read().split())
        except FileNotFoundError: return 0

    def mine(self):
        """Stand-in listeners of this test (its own lock path), never the real one."""
        r = subprocess.run(["pgrep", "-f", f"listen.py --lock {self.env['VOICE_LOCK']}"], capture_output=True, text=True)
        return r.stdout.split()

    def test_start_status_stop(self):
        self.assertIn("listener started", self.run_bin("listen", "--daemon", check=True).stdout)
        time.sleep(0.5)
        self.assertIn("listener running", self.run_bin("listen", "--status").stdout)
        self.assertEqual(len(self.mine()), 1)
        self.assertIn("listener stopped", self.run_bin("listen", "--stop").stdout)
        time.sleep(0.5)
        self.assertEqual(self.mine(), [])
        self.assertIn("not running", self.run_bin("listen", "--status").stdout)

    def test_only_one_listener(self):
        self.run_bin("listen", "--daemon", check=True); time.sleep(0.5)
        self.run_bin("listen", "--daemon", check=True); time.sleep(0.5)
        self.assertEqual(len(self.mine()), 1)

    def test_a_crashing_listener_is_restarted(self):
        self.env["FAKE_LISTENER"] = "crash"
        self.run_bin("listen", "--daemon")
        t = time.time()
        while self.starts_count() < 2 and time.time() - t < 10: time.sleep(0.2)
        self.assertGreaterEqual(self.starts_count(), 2)
        self.assertIn("ERROR listener exited (3); restarting in 3 s", self.read(self.voice_home, "listen.log"))

    def test_stop_leaves_other_installs_alone(self):
        other = subprocess.Popen(["python3", "-c", "import time; time.sleep(300)", "listen.py", "--lock", "/elsewhere.lock"])
        self.addCleanup(other.kill)
        self.run_bin("listen", "--daemon", check=True); time.sleep(0.5)
        self.run_bin("listen", "--stop")
        self.assertIsNone(other.poll())                         # still running

    def test_log_rolls_over(self):
        with open(os.path.join(self.voice_home, "listen.log"), "w") as f: f.write("x" * 2000)
        self.run_bin("listen", "--daemon", env={"VOICE_LOG_MAX": "1000"}, check=True)
        self.assertTrue(os.path.exists(os.path.join(self.voice_home, "listen.log.1")))


class FrontDoors(VoiceTest):
    def test_voice_mute_unmute_status(self):
        self.run_bin("voice", "mute", check=True)
        self.assertIn("MUTED", self.run_bin("voice", "status").stdout)
        self.run_bin("voice", "unmute", check=True)
        self.assertIn("unmuted", self.run_bin("voice", "status").stdout)

    def test_voice_focus(self):
        key = self.add_session("payments")
        self.assertIn(key, self.run_bin("voice", "focus", "payments").stdout)
        self.assertEqual(self.read(self.voice_home, "focus"), key)

    def test_sessions_lists_and_prunes(self):
        self.add_session("payments", status="busy")
        self.claude_session_file("sid-payments", "payments")          # sessions also checks Claude's own session file
        dead = self.add_session("ghost", pid=999999)
        out = self.run_bin("sessions").stdout
        self.assertIn("payments", out); self.assertIn("busy", out); self.assertNotIn("ghost", out)
        self.assertFalse(os.path.exists(os.path.join(self.shared, "sessions", dead + ".json")))

    def test_claude_voice_status_without_a_listener(self):
        out = self.run_bin("claude-voice", "status").stdout
        self.assertIn("listener not running", out)

    def test_help(self):
        self.assertIn("claude-voice config", self.run_bin("claude-voice", "help").stdout)


class StopEverything(VoiceTest):
    def test_stop_unloads_the_model_and_asks_the_player_to_quit(self):
        self.fake_ollama()
        self.env.update(CLAUDE_VOICE_PLATFORM="wsl")              # the Windows player exists only on WSL
        with open(os.path.join(self.shared, "player.alive"), "w") as f: f.write("1")
        out = self.run_bin("claude-voice", "stop").stdout
        self.assertIn("listener stopped", out)
        self.assertIn("Windows speech player stopped", out)
        self.assertTrue(os.path.exists(os.path.join(self.shared, "player.quit")))
