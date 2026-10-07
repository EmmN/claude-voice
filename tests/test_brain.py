"""The local brain inside routing, and speechify's summary mode, against a fake ollama (tests/helpers.FakeOllama)."""
import json, os
from helpers import VoiceTest


def answer(**kw): return json.dumps(kw)


class BrainRouting(VoiceTest):
    def setUp(self):
        super().setUp(); self.fake_ollama()
        self.add_session("payments"); self.add_session("calendar_sync")

    def brain(self, **decision):
        self.ollama.chat = lambda text: answer(**decision)

    def test_answer(self):
        self.brain(action="answer", text="Two sessions are open: payments and calendar sync.")
        self.assertEqual(self.dispatch("how many are there"), "answer | Two sessions are open: payments and calendar sync.")
        self.assertIn('session "payments"', self.ollama.calls[0])            # the facts it was given

    def test_route_to_the_named_session(self):
        self.brain(action="route", session="calendar sync")
        self.assertEqual(self.dispatch("have the calendar one look at the calendar export"),
                         "sent calendar sync | have the calendar one look at the calendar export")

    def test_an_answer_that_promises_to_route_is_routed(self):
        self.brain(action="answer", text="I will route that to the session.")
        self.assertEqual(self.dispatch("explain how the payments engine works", no_focus=True), "nomatch which session")

    def test_escalate_without_and_with_a_claude_brain(self):
        self.brain(action="escalate")
        self.assertTrue(self.dispatch("explain the CAP theorem").startswith("answer | I can't answer that one here"))
        with open(os.path.join(self.shared, "jarvis_brain"), "w") as f: f.write(f"{self.distro}__sid-payments")
        out = self.dispatch("explain the CAP theorem")
        self.assertTrue(out.startswith("ask payments | Question for Jarvis from the user"), out)

    def test_brain_down_falls_back_to_the_focused_session(self):
        self.ollama.fail = True
        with open(os.path.join(self.voice_home, "focus"), "w") as f: f.write(f"{self.distro}__sid-payments")
        self.assertEqual(self.dispatch("run the tests"), "sent payments | run the tests")


class BrainSessionCommands(VoiceTest):
    """Free-form session commands the fixed phrases miss: the brain names the intent, dispatch takes it from there."""
    def setUp(self):
        super().setUp(); self.fake_ollama()
        self.project("webshop", "mobile_app", "teamhub-widgets")
        o = f"{self.projects}/webshop"
        self.transcript(o, "j1", custom_title="flaky_tests", age_days=0.1)
        self.transcript(o, "s1", custom_title="webhook_retries", age_days=2)

    def brain(self, **decision):
        self.ollama.chat = lambda text: answer(**decision) if "Classify what the user just said" in text else '{"index": -1}'

    def head_row(self, text):
        head, _, body = self.dispatch(text).partition(" | ")
        return head, (json.loads(body) if body[:1] in "[{" and body else body)

    def test_past(self):
        self.brain(action="past", project="webshop")
        head, rows = self.head_row("what was I working on in webshop before")
        self.assertEqual((head, rows[0]["title"]), ("history", "flaky_tests"))

    def test_resume(self):
        self.brain(action="resume", query="webhook retries", project="")
        head, row = self.head_row("what was I doing about webhook retries")
        self.assertEqual((head, row["title"]), ("resume", "webhook_retries"))

    def test_a_project_that_is_no_folder_is_what_it_was_about(self):
        self.brain(action="past", project="flaky tests")
        head, row = self.head_row("pull up that thing I did on the flaky tests")
        self.assertEqual((head, row["title"]), ("resume", "flaky_tests"))

    def test_open(self):
        self.brain(action="open", project="widgets", name="", request="run the tests")
        self.assertEqual(self.dispatch("fire up a new one for the widgets repo and have it run the tests"),
                         f"open {self.projects}/teamhub-widgets | teamhub-widgets | run the tests |")

    def test_a_name_that_is_a_folder_is_the_project(self):
        self.brain(action="open", project="", name="mobile app", request="")
        self.assertTrue(self.dispatch("I need a fresh console for mobile app").startswith(f"open {self.projects}/mobile_app |"))

    def test_close(self):
        self.add_session("payments")
        self.brain(action="close", session="payments")
        self.assertTrue(self.dispatch("I'm done with payments, shut it").startswith("close payments | "))


class ModelPicks(VoiceTest):
    """When words match no folder or session title, the local model chooses from the list (ai_pick)."""
    def setUp(self):
        super().setUp(); self.fake_ollama(); self.project("scheduler-android", "webshop")

    def test_folder(self):
        def chat(text):
            if "Which of these project folders" not in text: return answer(action="route", session="")
            options = [l.split(": ", 1)[1] for l in text.splitlines() if l[:1].isdigit()]
            return answer(index=options.index("scheduler-android"))
        self.ollama.chat = chat
        self.assertTrue(self.dispatch("start a session in the scheduler for android").startswith(
            f"open {self.projects}/scheduler-android |"))

    def test_session(self):
        self.transcript(f"{self.projects}/webshop", "w1", custom_title="webhook_retries")
        self.transcript(f"{self.projects}/webshop", "p1", custom_title="policy_review")
        def chat(text):
            if "past Claude Code sessions" in text or "past Claude Code session" in text:
                options = [l.split(": ", 1)[1] for l in text.splitlines() if l[:1].isdigit()]
                return answer(index=next(i for i, o in enumerate(options) if o.startswith("webhook_retries")))
            return answer(action="route", session="")
        self.ollama.chat = chat
        head, _, body = self.dispatch("resume the money matching thing").partition(" | ")
        self.assertEqual((head, json.loads(body)["title"]), ("resume", "webhook_retries"))

    def test_model_says_none(self):
        self.ollama.chat = lambda text: answer(index=-1) if "Which of these" in text else answer(action="route", session="")
        self.assertEqual(self.dispatch("start a session in something unknown"), "nomatch which project | something unknown")


class SummaryMode(VoiceTest):
    LONG = "I fixed the flaky test in the booking spec and all specs pass now. " * 8

    def setUp(self):
        super().setUp(); self.fake_ollama()
        self.env.update(SPEECHIFY_MODE="summary")

    def test_the_model_retells_long_replies(self):
        self.ollama.generate = lambda prompt: "Fixed the flaky booking test; everything passes. Backport it?"
        out = self.run_bin("speechify", stdin=self.LONG, check=True).stdout.strip()
        self.assertEqual(out, "Fixed the flaky booking test; everything passes. Backport it?")

    def test_short_replies_are_read_as_is(self):
        self.assertEqual(self.run_bin("speechify", stdin="All done.", check=True).stdout.strip(), "All done.")
        self.assertEqual(self.ollama.calls, [])

    def test_ollama_down_falls_back_and_is_skipped_for_a_while(self):
        self.ollama.fail = True
        out = self.run_bin("speechify", stdin=self.LONG, check=True).stdout.strip()
        self.assertTrue(out.startswith("I fixed the flaky test"))
        self.assertTrue(os.path.exists(os.path.join(self.voice_home, "ollama_down")))
        n = len(self.ollama.calls); self.ollama.fail = False
        self.run_bin("speechify", stdin=self.LONG, check=True)
        self.assertEqual(len(self.ollama.calls), n)                      # not asked again within SPEECHIFY_RETRY

    def test_default_mode_reads_the_reply_without_ollama(self):
        self.env.pop("SPEECHIFY_MODE")
        self.run_bin("speechify", stdin=self.LONG, check=True)
        self.assertEqual(self.ollama.calls, [])
