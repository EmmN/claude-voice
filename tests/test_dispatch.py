"""dispatch: how a spoken sentence is routed (fixed phrases only; the local brain is off in tests)."""
import json, os
from helpers import VoiceTest


class Routing(VoiceTest):
    def setUp(self):
        super().setUp()
        self.add_session("standup_notes"); self.add_session("calendar_sync"); self.add_session("payments")

    def test_list_sessions(self):
        self.assertEqual(self.dispatch("list sessions"), "list 3 | calendar sync, payments, standup notes")

    def test_tell_names_the_session_and_strips_the_name(self):
        self.assertEqual(self.dispatch("tell payments to check the logs"), "sent payments | check the logs")

    def test_for_the_x_session_and_the_x_one(self):
        self.assertEqual(self.dispatch("for the payments session, check the logs"), "sent payments | check the logs")
        self.assertEqual(self.dispatch("on the standup notes one ask claude what else I need to do today"),
                         "sent standup notes | ask claude what else I need to do today")

    def test_polite_start_does_not_matter(self):
        self.assertEqual(self.dispatch("can you tell payments to check the logs"), "sent payments | check the logs")

    def test_misheard_tell(self):
        self.assertEqual(self.dispatch("stell payments, check the logs"), "sent payments | check the logs")
        self.assertEqual(self.dispatch("tel standup notes to check the logs"), "sent standup notes | check the logs")

    def test_name_heard_split_into_words(self):
        self.add_session("webshop-7c")
        self.assertEqual(self.dispatch("tell web shop 7c to check the logs"), "sent webshop 7c | check the logs")

    def test_name_then_comma_then_request(self):
        self.assertEqual(self.dispatch("payments, check the logs"), "sent payments | check the logs")
        self.assertEqual(self.dispatch("standup notes: run the report"), "sent standup notes | run the report")

    def test_trailing_never_mind_cancels(self):
        for t in ("Web shop. Standup. Never mind.", "tell payments to check the logs, forget it", "start a session, cancel that"):
            self.assertEqual(self.dispatch(t), "cancel", t)

    def test_switch_and_bare_name(self):
        self.assertEqual(self.dispatch("switch to payments"), "focus payments")
        self.assertEqual(self.dispatch("calendar sync"), "focus calendar sync")
        self.assertEqual(self.dispatch("switch to payments and run the tests"), "focus payments | run the tests")

    def test_broadcast(self):
        self.assertEqual(self.dispatch("all sessions, summarize your work"), "broadcast 3 | summarize your work")

    def test_unnamed_request_needs_a_session_when_several_are_open(self):
        self.assertEqual(self.dispatch("run the tests", no_focus=True), "nomatch which session")

    def test_cancel_words(self):
        for t in ("stop", "never mind", "no", "be quiet", "enough", "hush", "that's all", "Nothing right now, we're good."):
            self.assertEqual(self.dispatch(t), "cancel", t)

    def test_cancel_needs_the_whole_sentence(self):
        # a "we're good" that also asks for something is not a cancel (the brain, off in tests, would route it)
        self.assertNotEqual(self.dispatch("we're good, tell payments to commit"), "cancel")

    def test_close_and_stop_need_every_word_to_name_a_session(self):
        key = self.dispatch("close the payments session").split(" | ")[1]
        self.assertEqual(self.dispatch("close the payments session"), f"close payments | {key}")
        self.assertTrue(self.dispatch("stop standup notes").startswith("close standup notes | "))
        self.assertTrue(self.dispatch("kill calendar sync").startswith("close calendar sync | "))
        self.assertEqual(self.dispatch("close the pull request", no_focus=True), "nomatch which session")
        self.assertEqual(self.dispatch("stop the tests", no_focus=True), "nomatch which session")

    def test_dead_sessions_are_forgotten(self):
        key = self.add_session("ghost", pid=999999)
        self.assertNotIn("ghost", self.dispatch("list sessions"))
        self.assertFalse(os.path.exists(os.path.join(self.shared, "sessions", key + ".json")))

    def test_state_json(self):
        st = json.loads(self.run_bin("dispatch", "--state").stdout)
        self.assertEqual(sorted(st["sessions"]), ["calendar sync", "payments", "standup notes"])


class Delivery(VoiceTest):
    def test_sending_appends_to_the_inbox(self):
        key = self.add_session("payments")
        out = self.dispatch("tell payments to check the logs", dry=False)
        self.assertTrue(out.startswith("sent payments | check the logs"), out)
        self.assertEqual(self.read(self.shared, "inbox", key + ".msg"), "check the logs\n")

    def test_queued_when_the_inbox_hook_is_not_running(self):
        self.add_session("payments")
        self.assertEqual(self.dispatch("tell payments to say hello", dry=False), "sent payments | say hello | queued")

    def test_not_queued_when_the_waiter_runs(self):
        self.add_session("payments", sid="s1")
        with open(os.path.join(self.voice_home, "sessions", ".s1.waiter"), "w") as f: f.write(str(os.getpid()))
        self.assertEqual(self.dispatch("tell payments to say hello", dry=False), "sent payments | say hello")

    def test_no_sessions(self):
        self.assertEqual(self.dispatch("run the tests"), "nomatch no sessions")


class OpenAndClose(VoiceTest):
    def setUp(self):
        super().setUp()
        self.project("webshop", "teamhub-deploy", "teamhub-widgets", "teamhub-widget-examples", "mobile_app", "time_lister")

    def test_open_with_name_and_request(self):
        out = self.dispatch("Start a session in teamhub deploy called billing and ask it to check the failing specs")
        self.assertEqual(out, f"open {self.projects}/teamhub-deploy | billing | check the failing specs |")

    def test_open_without_name_uses_the_folder(self):
        self.assertEqual(self.dispatch("please open a new session for mobile app"),
                         f"open {self.projects}/mobile_app | mobile_app |  |")

    def test_folder_words_with_spaces_removed(self):
        self.assertTrue(self.dispatch("start a session in team hub widgets").startswith(f"open {self.projects}/teamhub-widgets |"))
        self.assertTrue(self.dispatch("start a session in web shop called billing").startswith(f"open {self.projects}/webshop | billing"))

    def test_unknown_folder(self):
        self.assertEqual(self.dispatch("start a session in nonexistent thing"), "nomatch which project | nonexistent thing")

    def test_no_project_offers_the_most_used(self):
        self.transcript(f"{self.projects}/webshop", "a1", ai_title="x")
        self.transcript(f"{self.projects}/webshop", "a2", ai_title="y")
        self.transcript(f"{self.projects}/teamhub-deploy", "b1", ai_title="z")
        head, _, body = self.dispatch("start a new session").partition(" | ")
        self.assertEqual(head, "pickproject")
        self.assertEqual([t["project"] for t in json.loads(body)["top"]], ["webshop", "teamhub-deploy"])


class PastSessions(VoiceTest):
    def setUp(self):
        super().setUp()
        self.project("webshop", "teamhub-deploy")
        o = f"{self.projects}/webshop"
        self.transcript(o, "j1", custom_title="flaky_tests", age_days=0.1)
        self.transcript(o, "j2", custom_title="flaky_tests_review", age_days=0.2)
        self.transcript(o, "s1", ai_title="Webhook retries", age_days=3)
        self.transcript(f"{self.projects}/teamhub-deploy", "m1", custom_title="db_upgrade", age_days=1)

    def row(self, text, **kw):
        head, _, body = self.dispatch(text, **kw).partition(" | ")
        return head, (json.loads(body) if body[:1] in "[{" and body else body)

    def test_list_old_sessions_anywhere_in_the_sentence(self):
        for t in ("list the old sessions in webshop", "can you give me old Claude sessions from Webshop?",
                  "AGR with Can You List the Old Sessions from WebShop?"):
            head, rows = self.row(t)
            self.assertEqual(head, "history", t)
            self.assertEqual([r["title"] for r in rows][:2], ["flaky_tests", "flaky_tests_review"], t)

    def test_resume_exact_title_wins(self):
        head, row = self.row("resume flaky tests")
        self.assertEqual((head, row["title"]), ("resume", "flaky_tests"))
        head, row = self.row("could you resume flaky tests review")
        self.assertEqual((head, row["title"]), ("resume", "flaky_tests_review"))

    def test_resume_close_matches_to_choose_from(self):
        head, rows = self.row("resume the flaky one")
        self.assertEqual(head, "history")
        self.assertIn("flaky_tests", [r["title"] for r in rows])

    def test_resume_the_last_session_in_a_project(self):
        head, row = self.row("resume the last session in teamhub deploy")
        self.assertEqual((head, row["title"]), ("resume", "db_upgrade"))

    def test_continue_is_a_request_unless_the_title_matches(self):
        self.add_session("payments")
        self.assertEqual(self.dispatch("continue with the tests"), "sent payments | continue with the tests")
        head, row = self.row("continue flaky tests")
        self.assertEqual((head, row["title"]), ("resume", "flaky_tests"))


class DoneButClose(VoiceTest):
    def test_im_done_with_x_close_it_is_not_a_cancel(self):
        self.add_session("payments")
        # found by test_brain: "I'm done" made these a cancel before the brain could see the close
        for t in ("I'm done with payments, close it", "I'm done with payments, shut it", "we're done, resume flaky tests"):
            self.assertNotEqual(self.dispatch(t), "cancel", t)


class ClaudeBrainIsTheDefault(VoiceTest):
    def test_unnamed_requests_go_to_the_brain_instead_of_which_session(self):
        self.add_session("webshop_7c"); key = self.add_session("jarvis")
        with open(os.path.join(self.shared, "jarvis_brain"), "w") as f: f.write(key)
        out = self.dispatch("can you tell me more about yourself", no_focus=True)
        self.assertTrue(out.startswith("ask jarvis | Request for Jarvis from the user"), out)

    def test_without_a_brain_it_still_asks(self):
        self.add_session("webshop_7c"); self.add_session("jarvis")
        self.assertEqual(self.dispatch("can you tell me more about yourself", no_focus=True), "nomatch which session")

    def test_a_named_session_still_wins(self):
        self.add_session("webshop_7c"); key = self.add_session("jarvis")
        with open(os.path.join(self.shared, "jarvis_brain"), "w") as f: f.write(key)
        self.assertEqual(self.dispatch("tell webshop 7c to run the tests", no_focus=True), "sent webshop 7c | run the tests")
