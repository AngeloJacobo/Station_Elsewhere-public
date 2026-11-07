"""Auto reels planner: history file, editorial slots and validation, with ChatGPT mocked (no usage)."""
import random
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from app import reels

GOOD = ["The bus driver waves at you", "At the same corner every morning", "You never learned his name",
        "And he never learned yours", "But on the day", "He wasn't there", "The whole ride felt wrong",
        "Some people matter quietly", "Before you notice them"]


def reply(slot, title, lines=GOOD):
    return {"slot": slot, "title": title, "topic": "t", "motif": "m", "setting": "s", "lines": lines, "uniqueness": "u"}


class ReelsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = patch.object(reels, "HISTORY_FILE", Path(self.temp.name) / "reels_history.md")
        p.start()
        self.addCleanup(p.stop)

    def test_history_round_trip_and_status(self):
        [e] = reels.append_history([{"title": "A small thing", "lines": ["one", "two"], "pillar": "Nostalgia"}])
        self.assertTrue(reels.set_status(e["id"], "exported · exports/a.mp4 · 19.8 s"))
        [back] = reels.read_history()
        self.assertEqual(back["title"], "A small thing")
        self.assertEqual(back["lines"], ["one", "two"])
        self.assertEqual(back["pillar"], "Nostalgia")
        self.assertEqual(back["status"], "exported · exports/a.mp4 · 19.8 s")
        self.assertFalse(reels.set_status("missing", "x"))

    def test_slots_follow_pillar_shares_and_never_repeat_back_to_back(self):
        slots = reels.plan_slots([], 20, random.Random(1))
        shares = Counter(s["pillar"] for s in slots)
        for pillar, (share, _) in reels.PILLARS.items():
            self.assertAlmostEqual(shares[pillar], share / 100 * 20, delta=1)
        for a, b in zip(slots, slots[1:]):
            self.assertNotEqual(a["pillar"], b["pillar"])
            self.assertNotEqual(a["structure"], b["structure"])
            self.assertNotEqual(a["mood"] in reels.DEEP_MOODS, b["mood"] in reels.DEEP_MOODS)

    def test_slots_continue_from_history(self):
        history = [{"pillar": "Observation / reflection", "structure": "Tiny observation", "mood": "warm"}]
        first = reels.plan_slots(history, 1, random.Random(2))[0]
        self.assertNotEqual(first["pillar"], "Observation / reflection")
        self.assertNotEqual(first["structure"], "Tiny observation")
        self.assertIn(first["mood"], reels.DEEP_MOODS)

    def test_chosen_genre_is_used_for_every_reel(self):
        slots = reels.plan_slots([{"pillar": "Nostalgia"}], 4, random.Random(3), genre="Nostalgia")
        self.assertEqual({s["pillar"] for s in slots}, {"Nostalgia"})
        for a, b in zip(slots, slots[1:]):
            self.assertNotEqual(a["structure"], b["structure"])
        self.assertIn("genre = Nostalgia", reels.build_prompt([(1, slots[0])], []))

    def test_validation(self):
        hard, soft = reels.problems({"title": "A title that works well", "lines": GOOD[:5]}, set())
        self.assertTrue(any("at least 8" in h for h in hard))
        hard, soft = reels.problems({"title": "Repeat me please now", "lines": GOOD}, {reels._norm("Repeat me please now")})
        self.assertTrue(hard)
        hard, soft = reels.problems({"title": "A title that works well", "lines": GOOD}, set())
        self.assertEqual((hard, soft), ([], []))

    def test_plan_retries_bad_reels_and_records_good_ones(self):
        calls = []

        def ask(prompt, cancelled):
            calls.append(prompt)
            if len(calls) == 1:
                return [reply(1, "Some routines quietly become your life"), reply(2, "Too short", GOOD[:3])]
            self.assertIn("FIX THESE PROBLEMS", prompt)
            self.assertIn("Some routines quietly become your life", prompt)     # batch titles are avoided
            return [reply(2, "The people you only know by sight")]

        result = reels.plan(2, ask=ask)
        self.assertEqual(len(calls), 2)
        self.assertEqual([r["title"] for r in result["reels"]],
                         ["Some routines quietly become your life", "The people you only know by sight"])
        history = reels.read_history()
        self.assertEqual(len(history), 2)
        self.assertTrue(all(h["pillar"] == "Life lessons" and h["id"] for h in history))
        self.assertIn("Some routines quietly become your life", reels.build_prompt([], history))

    def test_plan_raises_when_nothing_usable(self):
        reels.append_history([{"title": "Some days are meant to be survived", "lines": GOOD}])
        with self.assertRaises(reels.PlanError):
            reels.plan(1, attempts=2, ask=lambda p, c: [reply(1, "Some days are meant to be survived")])


if __name__ == "__main__":
    unittest.main()
