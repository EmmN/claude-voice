"""The listener's pure logic: wake phrase matching and stripping, picking from a list, speech detection."""
import time, unittest, unittest.mock
import helpers  # noqa: F401  (puts bin/ on sys.path for listen)
import numpy as np
import listen
from listen import FRAME, RATE


class FakeSource:
    """Frames from a list of levels (one 80 ms frame each): 0 = silence, else a tone of roughly that RMS."""
    def __init__(self, levels):
        self.frames = [(np.full(FRAME, lv * 1.0, dtype=np.float64)).astype(np.int16) for lv in levels]
    def frame(self):
        return self.frames.pop(0) if self.frames else None


class WakePhrase(unittest.TestCase):
    def test_strip_the_wake_word_or_what_is_left_of_it(self):
        for said, rest in [("Jarvis, how many sessions are open?", "how many sessions are open?"),
                           ("vis, tell mobile app to run tests", "tell mobile app to run tests"),
                           ("Hey Jarvis, list sessions", "list sessions"),
                           ("okay so hey jarvis list sessions", "list sessions"),
                           ("how many sessions are open?", "how many sessions are open?"),
                           ("is it raining", "is it raining")]:
            self.assertEqual(listen.strip_wake(said, "hey_jarvis"), rest, said)

    def test_anywhere_after_an_interrupted_reply(self):
        said = "so the migration ran and the tests are green Hey Jarvis stop"
        self.assertEqual(listen.strip_wake(said, "hey_jarvis"), said)                 # first words only: not found
        self.assertEqual(listen.strip_wake(said, "hey_jarvis", anywhere=True), "stop")

    def test_phrase_spotter_match(self):
        s = listen.PhraseSpotter("hey claude", lambda audio, hint: "")
        self.assertEqual(s.match("Hey Claude, how many sessions are open?"), "how many sessions are open?")
        self.assertEqual(s.match("hey cloud"), "")                       # misheard, still the phrase
        self.assertIsNone(s.match("Hey, how was your weekend? The cloud bill was high."))


class PhraseSpotterBursts(unittest.TestCase):
    """feed(): cut the stream into bursts of speech and transcribe each burst once."""
    def spot(self, levels, text):
        calls = []
        sp = listen.PhraseSpotter("hey claude", lambda audio, hint: (calls.append(len(audio)), text)[1])
        hits = [h for h in (sp.feed(f, 120) for f in FakeSource(levels).frames) if h]
        return hits, calls

    def test_burst_then_quiet(self):
        hits, calls = self.spot([0] * 5 + [800] * 12 + [0] * 10, "Hey Claude, list sessions.")
        self.assertEqual(len(calls), 1)
        rest, frames, talking = hits[0]
        self.assertEqual((rest, talking), ("list sessions.", False))
        self.assertGreaterEqual(len(frames), 12)

    def test_long_speech_is_cut_and_marked_still_talking(self):
        hits, calls = self.spot([800] * 60, "Hey Claude, tell mobile app to run all the tests and")
        self.assertTrue(hits[0][2])                             # 2.6 s cap reached while you were still talking

    def test_a_click_is_not_transcribed(self):
        hits, calls = self.spot([0] * 5 + [900] * 2 + [0] * 12, "Hey Claude")
        self.assertEqual((hits, calls), ([], []))

    def test_other_speech_is_not_a_wake(self):
        hits, calls = self.spot([800] * 12 + [0] * 10, "Hey, how was your weekend?")
        self.assertEqual((hits, len(calls)), ([], 1))


class Picking(unittest.TestCase):
    items = [{"title": "flaky_tests"}, {"title": "release_notes_review"},
             {"title": "Four parallel fixes and guardrail migration"}]

    def test_by_number_or_title_words(self):
        for answer, title in [("two", "release_notes_review"), ("the third one", "Four parallel fixes and guardrail migration"),
                              ("number one", "flaky_tests"), ("flaky", "flaky_tests"),
                              ("the guardrail one", "Four parallel fixes and guardrail migration")]:
            self.assertEqual((listen.pick(answer, self.items) or {}).get("title"), title, answer)

    def test_nothing_fits(self):
        self.assertIsNone(listen.pick("something else", self.items))

    def test_humanize_and_when(self):
        self.assertEqual(listen.humanize("flaky_tests-review"), "flaky tests review")
        self.assertEqual(listen.humanize("one two three four five six seven eight nine", 8), "one two three four five six seven eight…")
        self.assertEqual(listen.when(time.time() - 60), "today")


class SpeechDetection(unittest.TestCase):
    def test_record_until_silence(self):
        src = FakeSource([0] * 3 + [800] * 10 + [0] * 40)
        audio = listen.record_request(src, noise_floor=10, wait=3)
        self.assertGreater(len(audio), FRAME * 10)

    def test_nobody_answers(self):
        self.assertEqual(len(listen.record_request(FakeSource([0] * 60), noise_floor=10, wait=2)), 0)

    def test_still_talking_after_the_wake_word(self):
        talking, _ = listen.keeps_talking(FakeSource([600] * 20), noise_floor=10)
        self.assertTrue(talking)
        talking, _ = listen.keeps_talking(FakeSource([0] * 20), noise_floor=10)
        self.assertFalse(talking)


class NoiseIsNotSpeech(unittest.TestCase):
    """With the voice detector, loud noise neither starts, prolongs nor wakes anything (frames at level 700 are
    'noise' for the fake detector below, frames at 800 'speech')."""
    def setUp(self):
        p = unittest.mock.patch.object(listen, "speech_prob", lambda f: 0.05 if int(abs(f[0])) == 700 else 0.99)
        p.start(); self.addCleanup(p.stop)

    def test_noise_does_not_start_a_recording(self):
        self.assertEqual(len(listen.record_request(FakeSource([700] * 60), noise_floor=10, wait=2)), 0)

    def test_noise_after_you_stop_does_not_keep_it_open(self):
        audio = listen.record_request(FakeSource([800] * 10 + [700] * 40 + [800] * 30), noise_floor=10, wait=3)
        self.assertLess(len(audio), FRAME * 45)                 # ended in the noise, did not run on into the next words

    def test_noise_after_the_wake_word_is_not_talking(self):
        self.assertFalse(listen.keeps_talking(FakeSource([700] * 20), noise_floor=10)[0])

    def test_noise_bursts_are_not_transcribed(self):
        calls = []
        sp = listen.PhraseSpotter("hey claude", lambda a, h: (calls.append(1), "Hey Claude")[1])
        for f in FakeSource([700] * 15 + [0] * 10).frames: sp.feed(f, 120)
        self.assertEqual(calls, [])
