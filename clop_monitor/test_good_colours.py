#!/usr/bin/env python3
"""Offline unit tests for good_colours.py -- no network."""

import re
import unittest

from good_colours import (
    COLOURS,
    colour_for,
    colour_for_text,
    find_good_in_text,
)
from goods import BY_GAME_NAME, GOODS

UNCOLOURED = {"Forbidden Research", "Apotheosis Serum"}


class ColourTableTests(unittest.TestCase):
    def test_every_colour_names_a_real_good(self):
        self.assertEqual(sorted(set(COLOURS) - set(BY_GAME_NAME)), [])

    def test_every_colour_is_a_six_digit_hex(self):
        for name, value in COLOURS.items():
            with self.subTest(good=name):
                self.assertRegex(value, r"^#[0-9A-F]{6}$")

    def test_the_uncoloured_goods_are_the_dna_and_the_ascension_pair(self):
        # Fourteen goods have no colour. Naming them here means adding a colour
        # for one is a deliberate edit to this test, not a silent drift.
        missing = {good.game_name for good in GOODS} - set(COLOURS)
        dna = {name for name in missing if name.startswith("DNA - ")}
        self.assertEqual(len(dna), 12)
        self.assertEqual(missing - dna, UNCOLOURED)

    def test_colours_are_allowed_to_repeat(self):
        # Five pairs share a value on purpose; see the module docstring. This
        # pins the intent so a future reader does not "fix" it.
        repeated = {c for c in COLOURS.values() if list(COLOURS.values()).count(c) > 1}
        self.assertEqual(len(repeated), 5)


class ColourLookupTests(unittest.TestCase):
    def test_known_good(self):
        self.assertEqual(colour_for("Copper"), "#FF6A00")

    def test_uncoloured_good_is_none_not_a_default(self):
        self.assertIsNone(colour_for("DNA - North Zebrica"))
        self.assertIsNone(colour_for("Forbidden Research"))

    def test_unknown_name_is_none(self):
        self.assertIsNone(colour_for("Sunshine"))


class FindGoodInTextTests(unittest.TestCase):
    def test_finds_a_good_by_name(self):
        good = find_good_in_text("Your Copper mine produced 5 copper.")
        self.assertEqual(good.game_name, "Copper")

    def test_longer_name_wins_over_its_own_suffix(self):
        good = find_good_in_text("You are short on Machinery Parts.")
        self.assertEqual(good.game_name, "Machinery Parts")

    def test_earliest_mention_wins(self):
        good = find_good_in_text("Tungsten ran out, so Composites stopped.")
        self.assertEqual(good.game_name, "Tungsten")

    def test_matching_is_case_insensitive(self):
        good = find_good_in_text("your gasoline is low")
        self.assertEqual(good.game_name, "Gasoline")

    def test_word_boundaries_are_respected(self):
        # "Gem" must not match "Gems", and a good's name inside a longer word
        # must not match either.
        self.assertIsNone(find_good_in_text("The Gemstone Alliance declared war."))

    def test_no_good_mentioned(self):
        self.assertIsNone(find_good_in_text("Your nation is at war."))
        self.assertIsNone(colour_for_text("Your nation is at war."))

    def test_a_good_inside_a_building_name_still_matches(self):
        # Documented behaviour rather than an accident: a trim is a glance-level
        # hint, and "Basic Oil Well" being oil-coloured is the useful answer.
        good = find_good_in_text("Too many Basic Oil Wells cause environmental damage!")
        self.assertEqual(good.game_name, "Oil")

    def test_colour_for_text_returns_the_goods_colour(self):
        self.assertEqual(colour_for_text("Buy orders for Pies:"), "#FF00DC")

    def test_an_uncoloured_good_in_prose_gives_no_trim(self):
        self.assertIsNotNone(find_good_in_text("Forbidden Research reached 200."))
        self.assertIsNone(colour_for_text("Forbidden Research reached 200."))


if __name__ == "__main__":
    unittest.main()
