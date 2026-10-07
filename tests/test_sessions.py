"""recent-sessions (past sessions from the transcripts), session-open (the launcher) and session-close."""
import json, os, subprocess, time
from helpers import VoiceTest


class RecentSessions(VoiceTest):
    def setUp(self):
        super().setUp()
        self.project("webshop", "teamhub-deploy")
        o = f"{self.projects}/webshop"
        self.transcript(o, "j1", custom_title="flaky_tests", age_days=0.1)
        self.transcript(o, "j2", ai_title="CI pipeline", custom_title="flaky_tests_review", age_days=0.2)
        self.transcript(o, "s1", ai_title="Webhook retries", age_days=3)
        self.transcript(f"{self.projects}/teamhub-deploy", "m1", ai_title="DB upgrade", age_days=40)

    def rows(self, *args):
        return [json.loads(l) for l in self.run_bin("recent-sessions", *args, check=True).stdout.splitlines()]

    def test_newest_first(self):
        self.assertEqual([r["title"] for r in self.rows("--limit", "3")], ["flaky_tests", "flaky_tests_review", "Webhook retries"])

    def test_rename_wins_over_the_generated_title(self):
        self.assertIn("flaky_tests_review", [r["title"] for r in self.rows()])
        self.assertNotIn("CI pipeline", [r["title"] for r in self.rows()])

    def test_query_prefers_the_exact_title(self):
        top = self.rows("--query", "flaky tests", "--limit", "2")
        self.assertEqual(top[0]["title"], "flaky_tests")
        self.assertGreater(top[0]["score"], top[1]["score"])

    def test_fuzzy_query(self):
        self.assertEqual(self.rows("--query", "web hook retry", "--limit", "1")[0]["title"], "Webhook retries")

    def test_project_filter_tolerates_spaces(self):
        self.assertEqual({r["project"] for r in self.rows("--project", "web shop")}, {"webshop"})

    def test_top_projects_counts_the_last_30_days(self):
        top = self.rows("--top-projects", "5")
        self.assertEqual([(t["project"], t["count"]) for t in top], [("webshop", 3)])

    def test_open_sessions_are_marked(self):
        self.claude_session_file("j1", "flaky_tests")
        self.assertTrue(next(r for r in self.rows() if r["id"] == "j1")["open"])


class SessionOpen(VoiceTest):
    def launch(self, *args):
        return self.run_bin("session-open", *args, "--dry-run", check=True).stdout

    def test_new_session_starts_with_a_first_turn(self):
        out = self.launch(self.projects, "Billing Test")
        self.assertIn("export CLAUDE_VOICE_AUTO=1", out)
        self.assertIn("exec claude -n billing_test", out)
        self.assertIn("ready", out)                     # the first turn starts its inbox hook

    def test_request_is_quoted(self):
        out = self.launch(self.projects, "b", "check the specs; then report")
        self.assertIn(r"check\ the\ specs\;\ then\ report", out)

    def test_resume(self):
        out = self.launch(self.projects, "x", "--resume", "abc-123")
        self.assertIn("exec claude --resume abc-123", out)
        self.assertIn(r"where\ we\ left\ off", out)   # shell-quoted in the launch script


class SessionClose(VoiceTest):
    def test_ends_the_process_and_forgets_it(self):
        p = subprocess.Popen(["sleep", "300"])
        key = self.add_session("dummy", pid=p.pid)
        r = self.run_bin("session-close", key)
        self.assertEqual(r.stdout.strip(), "closed dummy")
        self.assertIsNotNone(p.wait(timeout=10))
        self.assertFalse(os.path.exists(os.path.join(self.shared, "sessions", key + ".json")))

    def test_unknown_session(self):
        self.assertNotEqual(self.run_bin("session-close", "nope").returncode, 0)
