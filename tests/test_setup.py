"""register-hooks: the hooks setup writes into ~/.claude/settings.json."""
import json, os
from helpers import VoiceTest


class RegisterHooks(VoiceTest):
    def setUp(self):
        super().setUp(); self.settings = os.path.join(self.home, ".claude", "settings.json")

    def register(self):
        self.run_bin("register-hooks", self.settings, check=True)
        with open(self.settings) as f: return json.load(f)

    def commands(self, d, event):
        return [h["command"] for e in d["hooks"].get(event, []) for h in e["hooks"]]

    def test_all_five_hooks(self):
        d = self.register(); b = f"{self.home}/.local/bin/claude-voice-hook-"
        self.assertEqual(self.commands(d, "Stop"), [b + "stop", b + "inbox"])
        self.assertEqual(self.commands(d, "UserPromptSubmit"), [b + "prompt"])
        self.assertEqual(self.commands(d, "SessionStart"), [b + "start"])
        self.assertEqual(self.commands(d, "SessionEnd"), [b + "end"])
        inbox = next(h for e in d["hooks"]["Stop"] for h in e["hooks"] if h["command"].endswith("inbox"))
        self.assertTrue(inbox["asyncRewake"]); self.assertEqual(inbox["rewakeSummary"], "🎤 Voice request (Jarvis)")

    def test_rerunning_never_duplicates(self):
        self.register(); d = self.register()
        self.assertEqual(len(self.commands(d, "Stop")), 2)

    def test_other_hooks_and_settings_stay(self):
        os.makedirs(os.path.dirname(self.settings), exist_ok=True)
        with open(self.settings, "w") as f:
            json.dump({"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "my-own-hook"}]},
                                                           {"hooks": [{"type": "command", "command": "/x/claude-speak-last"}]}]}}, f)
        d = self.register()
        self.assertEqual(d["model"], "opus")
        self.assertIn("my-own-hook", self.commands(d, "Stop"))
        self.assertFalse([c for c in self.commands(d, "Stop") if "claude-speak-last" in c])   # the old hook is replaced
