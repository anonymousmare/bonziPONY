#!/usr/bin/env python3
"""Offline unit tests for clop_pages.py -- no network.

The fixtures in ``fixtures/`` were rendered by the game's own PHP templates rather than
written by hand, so these tests check the parsers against what the game actually emits.
See ``fixtures/README.md``.
"""

import unittest
from pathlib import Path

from clop_pages import (
    PageParseError,
    parse_alliance,
    parse_messages,
    parse_nation,
    parse_news,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class NationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nation = parse_nation(fixture("viewnation_47.html"), nation_id=47)

    def test_header_fields(self):
        n = self.nation
        self.assertEqual(n.name, "Rustlung")
        self.assertEqual(n.region, "North Zebrica")
        self.assertEqual(n.government, "Authoritarianism")
        self.assertEqual(n.economy, "Free Market")
        self.assertEqual(n.leader, "anon88")
        self.assertEqual(n.age, 143)

    def test_alliance_link_gives_the_id_not_just_the_name(self):
        self.assertEqual(self.nation.alliance_name, "The Hoofprint")
        self.assertEqual(self.nation.alliance_id, 12)

    def test_gdp_is_read_past_the_thousands_separators(self):
        self.assertEqual(self.nation.gdp, 1_875_000)

    def test_every_building_and_its_count(self):
        self.assertEqual(self.nation.buildings, {
            "Advanced Factory": 12, "Gem Mine": 8,
            "Mechanized Copper Mine": 20, "Barracks": 3,
        })

    def test_forces_are_split_into_attackers_and_defenders(self):
        self.assertEqual([f.name for f in self.nation.attackers], ["First Lance"])
        self.assertEqual([f.name for f in self.nation.defenders],
                         ["Wall Watch", "The Ascended"])

    def test_a_force_carries_everything_warcalc_needs(self):
        lance = self.nation.attackers[0]
        self.assertEqual(lance.type, "Unicorns")
        self.assertEqual(lance.size, 40)
        self.assertEqual(lance.training, 12)
        self.assertEqual(lance.weapon, "Grid Squares")
        self.assertEqual(lance.armor, "Shining")

    def test_alicorns_have_no_gear_rendered_and_fall_back_to_scrounged(self):
        # The template omits weapon and armour for type 6 entirely, because the game
        # gives alicorns fixed stats regardless of what they carry.
        ascended = self.nation.defenders[1]
        self.assertEqual(ascended.type, "Alicorns")
        self.assertEqual(ascended.size, 5)
        self.assertEqual(ascended.weapon, "Scrounged Weapons")
        self.assertEqual(ascended.armor, "Scrounged Armor")

    def test_a_force_converts_straight_into_a_warcalc_input(self):
        self.assertEqual(self.nation.attackers[0].as_warcalc(), {
            "name": "First Lance", "type": "Unicorns", "size": 40,
            "training": 12, "weapon": "Grid Squares", "armor": "Shining",
        })

    def test_total_defence_counts_only_the_defenders(self):
        self.assertEqual(self.nation.total_defence, 65)   # 60 pegasi + 5 alicorns

    def test_the_economy_table_is_the_games_own_arithmetic(self):
        # Generated / Used / Net per tick, straight off the page.
        self.assertEqual(self.nation.economy_rows["Copper"], (100, 20, 80))
        self.assertEqual(self.nation.economy_rows["Gems"], (40, 0, 40))

    def test_a_negative_net_survives_the_comma_formatting(self):
        self.assertEqual(self.nation.economy_rows["Energy"], (0, 65, -65))

    def test_government_upkeep_appears_even_with_no_building_producing_it(self):
        # Gasoline is drained by the government, not by any building this nation owns.
        # Deriving the economy from building counts alone would miss it entirely --
        # which is why the page's own table is parsed instead.
        self.assertEqual(self.nation.economy_rows["Gasoline"], (0, 10, -10))

    def test_a_page_that_is_not_a_nation_raises(self):
        with self.assertRaises(PageParseError):
            parse_nation("<html><body>nothing here</body></html>")


class AllianceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.alliance = parse_alliance(fixture("viewalliance.html"), alliance_id=12)

    def test_name_and_members(self):
        self.assertEqual(self.alliance.name, "The Hoofprint")
        self.assertEqual(self.alliance.members, ("anon88", "Vashti"))

    def test_stasis_is_recorded_and_stripped_from_the_name(self):
        # The page renders it as "Vashti (Stasis)" in the same cell as the name.
        # A member in stasis cannot act, which is worth knowing before counting them.
        self.assertEqual(self.alliance.in_stasis, ("Vashti",))

    def test_nations_keep_their_id_and_region(self):
        self.assertEqual(self.alliance.nations,
                         (("Rustlung", 47, "Zebrica"), ("Saltmarch", 52, "Burrozil")))

    def test_the_combined_economy(self):
        self.assertEqual(self.alliance.economy_rows["Copper"], (300, 50, 250))


class MessageTests(unittest.TestCase):
    def setUp(self):
        self.html = fixture("messages.html")

    def test_the_inbox(self):
        inbox = parse_messages(self.html)
        self.assertEqual(len(inbox), 2)
        self.assertEqual(inbox[0].sender, "anon88")
        self.assertEqual(inbox[0].posted, "2026-08-23 19:40:00")
        self.assertIn("3100", inbox[0].body)

    def test_the_sentbox_is_not_mixed_into_the_inbox(self):
        # Both boxes are plain tables on one page. Without the split, every reply the
        # player has ever sent comes back as though somebody sent it to them.
        sent = parse_messages(self.html, box="sentbox")
        self.assertEqual(len(sent), 1)
        self.assertNotIn(sent[0].body, [m.body for m in parse_messages(self.html)])

    def test_the_split_is_not_fooled_by_the_dropdown(self):
        # There is an <option value="sentbox">Sentbox</option> above the inbox. Matching
        # that instead of the heading puts the split before the messages, not between them.
        self.assertGreater(len(parse_messages(self.html)), 0)


class NewsTests(unittest.TestCase):
    def test_every_row_newest_first(self):
        items = parse_news(fixture("news.html"))
        self.assertEqual(len(items), 2)
        self.assertIn("tariffs", items[0].message)
        self.assertEqual(items[0].posted, "2026-08-23 18:00:00")

    def test_an_empty_page_is_not_an_error(self):
        self.assertEqual(parse_news("<html><body></body></html>"), [])


if __name__ == "__main__":
    unittest.main()
