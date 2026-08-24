#!/usr/bin/env python3
"""The roster: who exists at all, and asking about a nation by name because of it.

Two halves. The store is tested on its own -- searching, staleness, and the fact that a
region is replaced rather than merged. Then the lookup layer is driven end to end against
the four real regional pages in ``clop_monitor/fixtures/``, with a stub bridge in place of
the game, because "she can now be asked about a nation she has never read" is the claim this
whole feature exists to make and the store alone does not prove it.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "clop_monitor"))

from clop_pages import parse_nation, parse_rankings
from core import clop_dossier, clop_roster
from core.clop_roster import (
    REGION_MODES,
    RosterStore,
    mode_for_region,
    modes,
    split_place,
)

FIXTURES = ROOT / "clop_monitor" / "fixtures"


def rows(name):
    return parse_rankings((FIXTURES / name).read_text(encoding="utf-8"))


class _Row:
    """The few fields of a ``RankedNation`` the store keeps."""

    def __init__(self, nation_id, name, user=""):
        self.nation_id = nation_id
        self.name = name
        self.user = user
        self.user_id = None
        self.region = self.subregion = self.government = self.economy = ""


class ModeTests(unittest.TestCase):
    def test_every_region_has_a_mode_and_back_again(self):
        for region, mode in REGION_MODES.items():
            self.assertEqual(clop_roster.region_for_mode(mode), region)
            self.assertEqual(mode_for_region(region), mode)

    def test_saddle_arabia_is_not_just_its_name_lowercased(self):
        # The one region whose mode you cannot derive from the region name.
        self.assertEqual(mode_for_region("Saddle Arabia"), "saddle")
        self.assertEqual(mode_for_region("saddle"), "saddle")

    def test_a_prefix_resolves_only_when_it_is_unambiguous(self):
        self.assertEqual(mode_for_region("zeb"), "zebrica")
        self.assertIsNone(mode_for_region("nowhere"))

    def test_the_boards_and_the_rosters_are_all_listed(self):
        every = modes()
        for mode in list(REGION_MODES.values()) + ["gdp", "longevity", "statues"]:
            self.assertIn(mode, every)


class PlaceTests(unittest.TestCase):
    """"Central Zebrica" is the game's own phrasing, so it has to be a question she can take.

    It is the heading viewnation.php puts on a nation. Asking for it came back as "no nation
    or player called 'Central Zebrica'", which is both wrong-sounding and a wasted lookup
    round in a turn the user is waiting on.
    """

    def test_a_band_and_a_region_in_either_order(self):
        self.assertEqual(split_place("Central Zebrica"), ("Central", "Zebrica"))
        self.assertEqual(split_place("Zebrica Central"), ("Central", "Zebrica"))

    def test_case_and_two_word_regions(self):
        self.assertEqual(split_place("north saddle arabia"), ("North", "Saddle Arabia"))

    def test_a_band_on_its_own_means_all_four_regions(self):
        self.assertEqual(split_place("Central"), ("Central", ""))

    def test_a_name_that_merely_starts_with_a_band_is_not_a_place(self):
        # Otherwise a nation called North Star becomes an empty region listing instead of
        # being found.
        self.assertIsNone(split_place("North Star"))

    def test_a_region_alone_is_not_a_place(self):
        # It is handled a step earlier, as a region; a band is what makes this a place.
        self.assertIsNone(split_place("Zebrica"))
        self.assertIsNone(split_place("nowhere"))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = RosterStore(Path(self.dir.name) / "r.json")
        self.store.record("Burrozil", rows("rankings_burrozil.html"))

    def tearDown(self):
        self.dir.cleanup()

    def test_a_region_lands_whole(self):
        self.assertEqual(len(self.store.in_region("Burrozil")), 19)
        self.assertEqual(len(self.store.nations), 19)

    def test_a_second_read_replaces_rather_than_merges(self):
        # A nation that has left the page has been conquered or has died. Keeping it would
        # have her briefing the user about somebody who no longer exists.
        self.store.record("Burrozil", rows("rankings_burrozil.html")[:3])
        self.assertEqual(len(self.store.in_region("Burrozil")), 3)

    def test_it_survives_a_restart(self):
        reopened = RosterStore(self.store.path)
        self.assertEqual(len(reopened.nations), 19)
        self.assertEqual(reopened.get(30)["name"], "Buenos Mares")

    def test_a_name_finds_a_nation(self):
        self.assertEqual(self.store.resolve("Buenos Mares")["nation_id"], 30)
        self.assertEqual(self.store.resolve("buenos")["nation_id"], 30)

    def test_an_exact_name_is_not_buried_under_the_names_containing_it(self):
        # Each tier answers alone. "Mare" must not come back as three maybes because two
        # other nations happen to have the word in them.
        only = RosterStore(Path(self.dir.name) / "only.json")
        only.record("Test", [
            _Row(1, "Mare"), _Row(2, "Mareconesia"), _Row(3, "Nightmare Moon"),
        ])
        self.assertEqual([e["nation_id"] for e in only.find("mare")], [1])
        self.assertEqual([e["nation_id"] for e in only.find("marec")], [2])   # prefix
        self.assertEqual(len(only.find("are")), 3)                            # substring

    def test_a_band_narrows_a_region(self):
        rows = self.store.in_place("South", "Burrozil")
        self.assertTrue(rows)
        self.assertEqual({e["subregion"] for e in rows}, {"South"})
        self.assertLess(len(rows), len(self.store.in_region("Burrozil")))

    def test_a_band_with_no_region_spans_what_is_on_file(self):
        self.assertEqual(
            len(self.store.in_place("North")),
            len([e for e in self.store.nations if e["subregion"] == "North"]),
        )

    def test_a_player_can_be_searched_for_too(self):
        # Players talk about each other by handle at least as often as by nation name.
        self.assertEqual([e["nation_id"] for e in self.store.find("Gold Meddle")], [30])

    def test_an_ambiguous_name_resolves_to_nothing(self):
        self.assertIsNone(self.store.resolve("the"))

    def test_one_region_out_of_four_is_not_a_roster(self):
        # Otherwise the first successful fetch makes her confident she knows everybody.
        self.assertTrue(self.store.is_stale())
        self.assertFalse(self.store.is_stale("Burrozil"))

    def test_all_four_regions_read_is_not_stale(self):
        for region in REGION_MODES:
            self.store.record(region, [_Row(1, "Somewhere")])
        self.assertFalse(self.store.is_stale())


class StubBridge:
    """The game, replaced by the captured rankings pages.

    ``refresh_roster`` here mirrors ``ClopBridge``'s: stubbing the fetch rather than the
    refresh keeps the region -> mode mapping inside what is under test.
    """

    available = True
    pages = {
        "saddle": "rankings_saddle.html",
        "zebrica": "rankings_zebrica.html",
        "burrozil": "rankings_burrozil.html",
        "przewalskia": "rankings_przewalskia.html",
        "gdp": "rankings_gdp.html",
        "longevity": "rankings_longevity.html",
    }

    class _Config:
        read_alliance_messages = False

    config = _Config()

    def __init__(self, roster):
        self.roster = roster
        self.fetched = []

    def rankings(self, mode):
        self.fetched.append(mode)
        page = self.pages.get(mode)
        return rows(page) if page else []

    def refresh_roster(self, force=False):
        for region, mode in REGION_MODES.items():
            if not force and not self.roster.is_stale(region):
                continue
            fetched = self.rankings(mode)
            if fetched:
                self.roster.record(region, fetched)
        return self.roster

    def nation(self, nation_id):
        # Somebody else's page, standing in for whoever was asked for: this test is about
        # how the id was arrived at, not about parsing viewnation.php again.
        self.fetched.append(f"viewnation:{nation_id}")
        return parse_nation((FIXTURES / "viewnation_47.html").read_text(encoding="utf-8"),
                            nation_id=nation_id)


class LookupTests(unittest.TestCase):
    """The tags themselves, through ``ToolRegistry.dispatch``."""

    def setUp(self):
        from core.clop_tools import ToolRegistry

        self.dir = tempfile.TemporaryDirectory()
        # The tool layer asks for the default store, so the default is what has to move --
        # otherwise these tests would read and overwrite the user's own roster and dossier.
        self._paths = (clop_roster.DEFAULT_PATH, clop_dossier.DEFAULT_PATH)
        clop_roster.DEFAULT_PATH = Path(self.dir.name) / "roster.json"
        clop_dossier.DEFAULT_PATH = Path(self.dir.name) / "dossier.json"
        clop_roster._STORES.clear()
        clop_dossier._STORES.clear()

        self.roster = clop_roster.store()
        self.bridge = StubBridge(self.roster)
        self.registry = ToolRegistry(self.bridge)

    def tearDown(self):
        clop_roster.DEFAULT_PATH, clop_dossier.DEFAULT_PATH = self._paths
        clop_roster._STORES.clear()
        clop_dossier._STORES.clear()
        self.dir.cleanup()

    def test_the_roster_lookup_lists_every_nation_in_the_game(self):
        answer = self.registry.dispatch("nations")
        self.assertNotIn("error:", answer)
        self.assertEqual(len(self.roster.nations), 46)
        self.assertIn("46 nation(s) in the game", answer)
        for region in REGION_MODES:
            self.assertIn(f"{region} (", answer)
        self.assertIn("Buenos Mares (#30)", answer)
        self.assertIn("Gold Meddle", answer)

    def test_a_region_narrows_it(self):
        answer = self.registry.dispatch("nations:burrozil")
        self.assertIn("Burrozil, 19 nation(s)", answer)
        self.assertNotIn("Starlight Reach", answer)   # Przewalskia

    def test_a_name_searches_it(self):
        answer = self.registry.dispatch("nations:Mareconesia")
        self.assertIn("One match", answer)
        self.assertIn("#49", answer)

    def test_a_part_of_a_region_is_a_question_she_can_take(self):
        """The exact lookup from the log that came back empty."""
        answer = self.registry.dispatch("nations:Central Zebrica")
        self.assertNotIn("No nation", answer)
        self.assertIn("Central Zebrica, 4 nation(s)", answer)
        self.assertIn("Vladihoofstock (#16)", answer)

    def test_a_band_alone_spans_every_region(self):
        answer = self.registry.dispatch("nations:Central")
        self.assertIn("every region", answer)
        self.assertIn("Saddle Arabia", answer)
        self.assertIn("Burrozil", answer)

    def test_a_name_starting_with_a_band_is_still_searched_as_a_name(self):
        answer = self.registry.dispatch("nations:North Star")
        self.assertIn("No nation or player called", answer)
        self.assertIn("North/Central/South", answer)

    def test_an_unknown_name_says_so_rather_than_inventing_one(self):
        answer = self.registry.dispatch("nations:Equestria")
        self.assertIn("No nation or player called", answer)

    def test_the_roster_is_read_once_and_then_reused(self):
        self.registry.dispatch("nations")
        before = list(self.bridge.fetched)
        self.assertEqual(sorted(before), sorted(REGION_MODES.values()))
        self.registry.dispatch("nations:zebrica")
        self.assertEqual(self.bridge.fetched, before, "a fresh roster was re-fetched")

    def test_a_scoreboard_comes_back_ranked(self):
        answer = self.registry.dispatch("rankings:gdp")
        self.assertIn("1. Starlight Reach (#4)", answer)
        self.assertIn("GDP Last Turn: 1,454,240", answer)

    def test_an_unknown_board_is_refused_before_it_is_fetched(self):
        # The game answers an unknown mode with an empty roster rather than an error, so an
        # unchecked typo would come back as a confident "nobody is on that board".
        answer = self.registry.dispatch("rankings:bogus")
        self.assertIn("error:", answer)
        self.assertNotIn("bogus", self.bridge.fetched)

    def test_a_nation_can_now_be_asked_for_by_name(self):
        """The point of the whole thing.

        Before the roster, a name she had not already read came back as "no nation called
        that on file" -- which was never what was asked. Now the name resolves to an id and
        the page is fetched.
        """
        answer = self.registry.dispatch("nation:Mareconesia")
        self.assertNotIn("error:", answer)
        self.assertIn("viewnation:49", self.bridge.fetched)

    def test_a_name_that_matches_several_asks_for_one(self):
        answer = self.registry.dispatch("nation:the")
        self.assertIn("error:", answer)
        self.assertIn("matches", answer)
        self.assertNotIn("viewnation:", " ".join(self.bridge.fetched))


if __name__ == "__main__":
    unittest.main()
