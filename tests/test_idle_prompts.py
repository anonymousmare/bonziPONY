#!/usr/bin/env python3
"""What she says when nothing is happening, and what the check-in switch takes away.

The idle pool is a list somebody will add a line to eventually. These tests are here so that
adding "ask if they have stretched today" to the wrong half fails immediately, rather than
turning up months later as a pony asking after your posture with the switch off.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.agent_loop import _CHECKIN_PROMPTS, _FLAVOUR_PROMPTS, _IDLE_PROMPTS, idle_prompts

#: The caretaker vocabulary. Not a blocklist the code consults -- a test that the split was
#: made on the axis it claims to be made on.
CARETAKER = (
    "eaten", "shower", "water", "outside", "fresh air", "errand",
    "chores", "obligations", "taken care of",
)


class SplitTests(unittest.TestCase):
    def test_the_two_halves_are_the_whole(self):
        self.assertEqual(_IDLE_PROMPTS, _CHECKIN_PROMPTS + _FLAVOUR_PROMPTS)

    def test_nothing_is_in_both(self):
        self.assertEqual(set(_CHECKIN_PROMPTS) & set(_FLAVOUR_PROMPTS), set())

    def test_switching_check_ins_off_removes_the_caretaking(self):
        """The user's actual request: stop being asked whether you have been outside."""
        remaining = " ".join(idle_prompts(check_ins=False)).casefold()
        for word in CARETAKER:
            with self.subTest(word=word):
                self.assertNotIn(word, remaining)

    def test_the_caretaking_is_still_there_when_it_is_on(self):
        on = " ".join(idle_prompts(check_ins=True)).casefold()
        self.assertIn("outside", on)
        self.assertIn("water", on)

    def test_she_is_never_left_with_nothing_to_say(self):
        # random.choice on an empty list raises, and the failure would be a silent pony.
        self.assertGreaterEqual(len(idle_prompts(check_ins=False)), 5)

    def test_every_prompt_still_asks_for_one_sentence(self):
        # The whole pool is written to keep an unprompted remark to one line; a new entry
        # without it is how spontaneous speech turns into a monologue.
        for prompt in _IDLE_PROMPTS:
            with self.subTest(prompt=prompt[:40]):
                self.assertIn("ONE sentence", prompt)


if __name__ == "__main__":
    unittest.main()
