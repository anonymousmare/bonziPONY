#!/usr/bin/env python3
"""Handing a battle to tools/warcalc.html without either side quietly changing it.

The failure this file is really about is silent. An equipment name the page does not
recognise does not raise in the browser -- ``makeRow`` leaves the select empty and the unit
fields scrounged gear -- so a mismatch shows up as her describing one battle while the page
displays a different one. Nothing errors, and the numbers are wrong.

So the tables are checked against the page itself, and against the game data every name comes
from, rather than against a list somebody typed here.
"""

import base64
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import warcalc_page as wp

PAGE = ROOT / "tools" / "warcalc.html"


def keys_of(name: str) -> set:
    """The keys of one of the page's own const tables. Bare or quoted: ``ACFU:4, "PRC-E6":1``."""
    return {a or b for a, b in
            (m for m in re.findall(r'(?:"([^"]+)"|([A-Za-z][\w.-]*))\s*:',
                                   _table_body(name)))}


def _table_body(name: str) -> str:
    text = PAGE.read_text(encoding="utf-8")
    match = re.search(rf"const {name} = \{{(.*?)\}};", text, re.S)
    assert match, f"{name} is no longer in the page in the shape this test reads"
    return match.group(1)


class TableTests(unittest.TestCase):
    """The Python copy of the page's key tables has to stay the page's key tables."""

    def test_weapon_keys_match_the_page(self):
        self.assertEqual(set(wp.WEAPON_KEYS), keys_of("WEAPON_IDS"))

    def test_armor_keys_match_the_page(self):
        self.assertEqual(set(wp.ARMOR_KEYS), keys_of("ARMOR_IDS"))

    def test_force_types_match_the_page(self):
        self.assertEqual(set(wp.FORCE_TYPES), keys_of("FORCE_TYPES"))

    def test_every_name_the_game_uses_maps_to_a_key(self):
        """The whole point: gamedata prints "Grid Squares", the page calls it GridSquares."""
        from core.clop_tools import gamedata

        data = gamedata()
        for row in data["weapons"]:
            with self.subTest(weapon=row["name"]):
                self.assertIn(wp._key(row["name"]), set(wp.WEAPON_KEYS))
        for row in data["armor"]:
            with self.subTest(armor=row["name"]):
                self.assertIn(wp._key(row["name"]), set(wp.ARMOR_KEYS))


class PayloadTests(unittest.TestCase):
    def test_a_force_arrives_in_the_pages_own_spelling(self):
        payload, notes = wp.build_payload(
            [{"type": "Unicorns", "weapon": "Grid Squares", "armor": "Shining",
              "size": 40, "training": 12}],
            [{"type": "Pegasi", "weapon": "Canopy Lights", "armor": "Dragon",
              "size": 60, "training": 6}],
        )
        self.assertEqual(notes, [])
        self.assertEqual(payload["attackers"], [
            {"type": "Unicorns", "weapon": "GridSquares", "armor": "Shining",
             "size": 40, "training": 12},
        ])
        self.assertEqual(payload["defenders"][0]["weapon"], "CanopyLights")
        self.assertTrue(payload["bonus"])

    def test_missing_gear_becomes_scrounged_rather_than_absent(self):
        # The tag lets weapon and armour be left off; the page's rows always have both.
        payload, notes = wp.build_payload([{"type": "Cavalry", "size": 10}], [])
        self.assertEqual(payload["attackers"][0]["weapon"], "ScroungedWeapons")
        self.assertEqual(payload["attackers"][0]["armor"], "ScroungedArmor")
        self.assertEqual(notes, [])

    def test_unrecognised_gear_is_reported_not_swallowed(self):
        # She has to be able to say so: the page will show scrounged gear either way, and
        # the difference between that and what she simulated is the user's whole answer.
        payload, notes = wp.build_payload(
            [{"type": "Cavalry", "weapon": "Friendship Cannon", "size": 10}], [])
        self.assertEqual(payload["attackers"][0]["weapon"], "ScroungedWeapons")
        self.assertTrue(any("Friendship Cannon" in n for n in notes), notes)

    def test_alicorns_cannot_be_handed_over_as_attackers(self):
        # The page's attacker dropdown has no such option, so the row would read as empty
        # and the battle would silently be short a force.
        payload, notes = wp.build_payload(
            [{"type": "Alicorns", "size": 5}], [{"type": "Alicorns", "size": 5}])
        self.assertEqual(payload["attackers"], [])
        self.assertEqual(len(payload["defenders"]), 1)
        self.assertTrue(any("only defend" in n for n in notes), notes)

    def test_an_unknown_force_type_is_dropped_with_a_reason(self):
        payload, notes = wp.build_payload([{"type": "Dragons", "size": 5}], [])
        self.assertEqual(payload["attackers"], [])
        self.assertTrue(any("Dragons" in n for n in notes), notes)

    def test_no_bonus_survives_into_the_payload(self):
        payload, _ = wp.build_payload([], [], defender_bonus=False)
        self.assertFalse(payload["bonus"])


class FragmentTests(unittest.TestCase):
    def decode(self, fragment: str):
        """Decode the way the page's b64urlToText does, padding included."""
        self.assertTrue(fragment.startswith("w="))
        raw = fragment[2:]
        padded = raw + "=" * ((4 - len(raw) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))

    def test_it_round_trips(self):
        payload, _ = wp.build_payload(
            [{"type": "Naval", "weapon": "Water Hammer", "armor": "Esohes",
              "size": 12, "training": 4}], [], title="Silverspire (#11)")
        self.assertEqual(self.decode(wp.fragment(payload)), payload)

    def test_a_name_with_quotes_and_accents_survives(self):
        # Nation names are player-chosen. Base64 is used precisely so no amount of
        # punctuation in one can break the URL.
        payload, _ = wp.build_payload([], [], title='naïve "quoted" — dash')
        self.assertEqual(self.decode(wp.fragment(payload))["title"], 'naïve "quoted" — dash')

    def test_the_url_points_at_the_real_page(self):
        payload, _ = wp.build_payload([{"type": "Tanks", "size": 3}], [])
        url = wp.page_url(payload)
        self.assertTrue(url.startswith("file://"))
        self.assertIn("warcalc.html#w=", url)


class PageTests(unittest.TestCase):
    def test_the_page_is_actually_shipped(self):
        self.assertTrue(PAGE.is_file(), f"{PAGE} is missing")

    def test_the_page_reads_the_fragment_this_module_writes(self):
        text = PAGE.read_text(encoding="utf-8")
        self.assertIn('raw.startsWith("w=")', text)
        self.assertIn("preloadFromHash", text)
        # A handed-over battle has to beat the autosave, or the link would look ignored.
        self.assertIn("if (!handedOver) restoreAutosave();", text)

    def test_opening_is_refused_rather_than_raised_when_the_page_is_missing(self):
        url, notes = wp.open_battle([{"type": "Tanks", "size": 3}], [],
                                    page=ROOT / "tools" / "not-here.html")
        self.assertIsNone(url)
        self.assertTrue(any("no warcalc page" in n for n in notes), notes)

    def test_an_empty_battle_is_not_opened(self):
        url, notes = wp.open_battle([], [])
        self.assertIsNone(url)
        self.assertTrue(any("nothing left to show" in n for n in notes), notes)


if __name__ == "__main__":
    unittest.main()
