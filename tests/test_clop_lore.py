#!/usr/bin/env python3
"""The lorebook: what it catches, what it deliberately does not, and what it costs."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.clop_lore import (
    AMBIGUOUS,
    MAX_ENTRIES,
    context_for,
    entries,
    find_mentions,
)


def names(text):
    return [e.name for e in find_mentions(text)]


class IndexTests(unittest.TestCase):
    def test_the_index_covers_every_kind(self):
        kinds = {e.kind for e in entries()}
        self.assertEqual(
            kinds,
            {"building", "good", "weapon", "armor", "unit", "government", "nation"},
        )

    def test_every_ambiguous_name_is_a_real_entity(self):
        # A typo here would silently make a name case-insensitive again.
        known = {e.name for e in entries()}
        self.assertEqual(sorted(AMBIGUOUS - known), [])

    def test_every_entry_renders(self):
        for entry in entries():
            with self.subTest(entry=entry.name):
                rendered = entry.render()
                self.assertTrue(rendered.startswith(entry.name))
                self.assertGreater(len(rendered), 20)


class MatchingTests(unittest.TestCase):
    def test_the_ordinary_case(self):
        self.assertEqual(names("what does a coffee farm cost"), ["Coffee Farm"])

    def test_matching_is_case_insensitive_for_unambiguous_names(self):
        self.assertEqual(names("how much COPPER do i have"), ["Copper"])
        self.assertEqual(names("is tungsten worth it"), ["Tungsten"])

    def test_ordinary_english_does_not_drag_game_data_in(self):
        # These are all real entity names, and all common words. Lowercase means
        # the person is talking, not asking about the game.
        for text in ("that's a dragon", "nope, don't", "I wonder", "at the bar",
                     "low energy today", "an oil change", "democracy is fine",
                     "a titan of industry", "it's a bit cooler today"):
            with self.subTest(text=text):
                self.assertEqual(names(text), [], f"{text!r} should match nothing")

    def test_the_same_words_capitalised_do_match(self):
        self.assertEqual(names("swap to Dragon armour"), ["Dragon"])
        self.assertEqual(names("Nope or Barding?"), ["Barding", "Nope"])

    def test_longer_names_win(self):
        # "Machinery Parts" must be tested before anything shorter it contains.
        self.assertIn("Machinery Parts", names("I need machinery parts"))

    def test_word_boundaries_are_respected(self):
        self.assertEqual(names("the Gemstone alliance"), [])

    def test_nothing_mentioned_yields_no_block(self):
        self.assertEqual(context_for("how was your day"), "")
        self.assertEqual(context_for(""), "")


class BudgetTests(unittest.TestCase):
    def test_a_message_naming_everything_is_capped(self):
        text = ("coffee farm, mall, oil fracker, barracks, gem mine, tungsten mine, "
                "bakery, statue, drug farm, toy factory")
        self.assertLessEqual(len(find_mentions(text)), MAX_ENTRIES)

    def test_the_block_stays_small_enough_to_prepend_every_turn(self):
        text = "coffee farm and a mall and machinery parts"
        block = context_for(text)
        self.assertLess(len(block), 2600)

    def test_the_block_says_where_it_came_from(self):
        block = context_for("what does a coffee farm cost")
        self.assertIn("4CLOP REFERENCE", block)
        self.assertIn("200,000 bits", block)

    def test_a_known_description_mismatch_is_flagged(self):
        # The Mall's own in-game description claims 400,000 GDP; the column says
        # 250,000. She should be told, not left to pick one.
        block = context_for("tell me about the Mall")
        self.assertIn("250,000", block.replace("+", ""))
        self.assertIn("NOTE:", block)


class LeakGuardTests(unittest.TestCase):
    def test_no_lorebook_entry_trips_the_prompt_leak_guard(self):
        """A reference block is prepended to a user turn, so it must not look like a leak."""
        from llm.response_parser import detect_prompt_leak

        for entry in entries():
            with self.subTest(entry=entry.name):
                self.assertIsNone(detect_prompt_leak(entry.render()))


if __name__ == "__main__":
    unittest.main()
