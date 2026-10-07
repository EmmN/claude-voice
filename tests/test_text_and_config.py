"""strip-markdown (what is read aloud), the platform layer and the settings file."""
import os, subprocess
from helpers import VoiceTest, BIN


class SpokenText(VoiceTest):
    def strip(self, text):
        return self.run_bin("strip-markdown", stdin=text, check=True).stdout.strip()

    def test_markdown_goes(self):
        self.assertEqual(self.strip("## Done\n\n**All** specs pass."), "Done\nAll specs pass.")

    def test_table_rows_become_sentences_and_dividers_disappear(self):
        self.assertEqual(self.strip("| Story | State |\n|---|---|\n| MT-1 | done |"), "Story, State.\nMT-1, done.")

    def test_code_links_ids_and_hashes(self):
        out = self.strip("Fixed `bin/hook-stop` (commit 1b835bc, PR #4123), see https://x.y. Done.")
        self.assertEqual(out, "Fixed hook-stop (commit, the PR), see the link. Done.")

    def test_paths_snake_case_and_units(self):
        out = self.strip("Set SAY_LOCK_WAIT in ~/projects/x/README.md; it waits 30 s and 300ms.")
        self.assertEqual(out, "Set SAY LOCK WAIT in README.md; it waits 30 seconds and 300 milliseconds.")

    def test_code_blocks_are_omitted(self):
        self.assertIn("(code omitted)", self.strip("Run:\n```\nmake test\n```\nthen push."))


class Platform(VoiceTest):
    def lib(self, expr, env=None):
        r = subprocess.run(["bash", "-c", f'. "{BIN}/lib.sh"; {expr}'], capture_output=True, text=True,
                           env={**self.env, **(env or {})})
        return r.stdout.strip()

    def test_mac_and_linux_keep_state_under_voice_home(self):
        env = {"VOICE_SHARED": "", "VOICE_LOCK": ""}
        for p in ("mac", "linux"):
            e = {k: v for k, v in self.env.items() if k not in ("VOICE_SHARED", "VOICE_LOCK")} | {"CLAUDE_VOICE_PLATFORM": p}
            r = subprocess.run(["bash", "-c", f'. "{BIN}/lib.sh"; echo "$VOICE_SHARED|$VOICE_LOCK|$(mic_input_args | tr "\\n" " ")"'],
                               capture_output=True, text=True, env=e).stdout.strip()
            shared, lock, mic = r.split("|")
            self.assertEqual((shared, lock), (f"{self.voice_home}/shared", f"{self.voice_home}/speech.lock"), p)
            self.assertIn("avfoundation" if p == "mac" else "pulse", mic)

    def test_portable_helpers(self):
        self.assertEqual(self.lib(f"pid_alive {os.getpid()} && echo yes"), "yes")
        self.assertEqual(self.lib("pid_alive 999999 || echo no"), "no")
        self.assertEqual(self.lib("newer_or_equal 2.5 2.4 && echo yes"), "yes")

    def test_config_file_and_environment_precedence(self):
        with open(os.path.join(self.voice_home, "config"), "w") as f:
            f.write("VOICE_WAKE=hey jarvis\n# a comment\nVOICE_SILENCE=3\n")
        self.assertEqual(self.lib('echo "$VOICE_WAKE|$VOICE_SILENCE"'), "hey jarvis|3")
        self.assertEqual(self.lib('echo "$VOICE_SILENCE"', env={"VOICE_SILENCE": "4"}), "4")

    def test_claude_voice_config_command(self):
        self.run_bin("claude-voice", "config", "VOICE_WAKE", "hey_jarvis", check=True)
        self.assertEqual(self.read(self.voice_home, "config"), "VOICE_WAKE=hey_jarvis\n")
        self.run_bin("claude-voice", "config", "VOICE_WAKE", "", check=True)
        self.assertEqual(self.read(self.voice_home, "config"), "")
