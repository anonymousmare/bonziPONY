#!/usr/bin/env python3
"""The dossier: what she remembers about other nations, and when it goes stale."""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "clop_monitor"))

from clop_pages import Force, Nation
from core.clop_dossier import DossierStore, store


def nation(nation_id=47, name="Rustlung", **kwargs):
    return Nation(
        nation_id=nation_id, name=name,
        region=kwargs.get("region", "North Zebrica"),
        government=kwargs.get("government", "Authoritarianism"),
        economy=kwargs.get("economy", "Free Market"),
        leader=kwargs.get("leader", "anon88"),
        alliance_id=12, alliance_name="The Hoofprint", age=143, gdp=1_875_000,
        buildings=kwargs.get("buildings", {"Gem Mine": 8}),
        forces=kwargs.get("forces", (
            Force("Wall Watch", "Pegasi", 60, 6, "Canopy Lights", "Dragon"),
            Force("First Lance", "Unicorns", 40, 12, "Grid Squares", "Shining", hostile=True),
        )),
        economy_rows=kwargs.get("economy_rows", {"Copper": (100, 20, 80)}),
    )


class NoticingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = DossierStore(Path(self.dir.name) / "d.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_a_sighting_makes_a_nation_worth_reading(self):
        self.assertTrue(self.store.notice(47, "Rustlung", why="bidding on Copper"))
        self.assertEqual(self.store.pending(), [47])

    def test_reading_one_clears_it_from_pending(self):
        self.store.notice(47, "Rustlung")
        self.store.record_nation(nation())
        self.assertEqual(self.store.pending(), [])

    def test_a_fresh_reading_makes_another_sighting_not_worth_a_fetch(self):
        self.store.record_nation(nation())
        self.assertFalse(self.store.notice(47, "Rustlung"),
                         "should not re-fetch a nation read minutes ago")

    def test_the_most_persistent_bidder_is_read_first(self):
        for _ in range(3):
            self.store.notice(52, "Saltmarch")
        self.store.notice(47, "Rustlung")
        self.assertEqual(self.store.pending(), [52, 47])

    def test_noticing_never_fetches(self):
        # notice() is called from the poll loop for every market order every minute.
        # It must stay cheap: recording only, no page fetch, no game contact.
        self.store.notice(99, "Somebody")
        self.assertIsNone(self.store.nation(99))


class StalenessTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = DossierStore(Path(self.dir.name) / "d.json", max_age_hours=6.0)

    def tearDown(self):
        self.dir.cleanup()

    def test_an_unread_nation_is_stale(self):
        self.assertTrue(self.store.is_stale(47))

    def test_a_just_read_nation_is_fresh(self):
        self.store.record_nation(nation())
        self.assertFalse(self.store.is_stale(47))

    def test_a_reading_goes_stale_with_age(self):
        self.store.record_nation(nation())
        old = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
        self.store._nations["47"]["read_at"] = old
        self.assertTrue(self.store.is_stale(47))

    def test_an_unreadable_timestamp_counts_as_stale(self):
        # Better to spend a fetch than to answer from a reading of unknown age.
        self.store.record_nation(nation())
        self.store._nations["47"]["read_at"] = "not a date"
        self.assertTrue(self.store.is_stale(47))


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "d.json"
        self.store = DossierStore(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def test_everything_needed_to_answer_survives(self):
        self.store.record_nation(nation())
        entry = self.store.nation(47)
        self.assertEqual(entry["name"], "Rustlung")
        self.assertEqual(entry["buildings"], {"Gem Mine": 8})
        self.assertEqual(entry["economy_rows"]["Copper"], [100, 20, 80])
        self.assertEqual(len(entry["forces"]), 2)

    def test_attackers_stay_distinguishable_from_defenders(self):
        # A force attacking a nation is not part of its defence, and counting it as one
        # would make every besieged nation look twice as strong as it is.
        self.store.record_nation(nation())
        forces = self.store.nation(47)["forces"]
        self.assertEqual([f["hostile"] for f in forces], [False, True])

    def test_it_survives_a_restart(self):
        self.store.record_nation(nation())
        reloaded = DossierStore(self.path)
        self.assertEqual(reloaded.nation(47)["name"], "Rustlung")

    def test_a_nation_with_no_id_is_not_recorded(self):
        self.store.record_nation(nation(nation_id=None))
        self.assertEqual(self.store.nations, [])


class LookupByNameTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = DossierStore(Path(self.dir.name) / "d.json")
        self.store.record_nation(nation(47, "Rustlung"))
        self.store.record_nation(nation(52, "Saltmarch"))

    def tearDown(self):
        self.dir.cleanup()

    def test_an_exact_name(self):
        self.assertEqual(self.store.find_by_name("Rustlung")["nation_id"], 47)

    def test_case_does_not_matter(self):
        self.assertEqual(self.store.find_by_name("rustlung")["nation_id"], 47)

    def test_an_unambiguous_fragment(self):
        self.assertEqual(self.store.find_by_name("salt")["nation_id"], 52)

    def test_an_unknown_name_is_none_rather_than_a_guess(self):
        self.assertIsNone(self.store.find_by_name("Nobody"))

    def test_an_ambiguous_fragment_refuses_rather_than_picking(self):
        self.store.record_nation(nation(60, "Saltford"))
        self.assertIsNone(self.store.find_by_name("salt"))


class SharedStoreTests(unittest.TestCase):
    def test_one_store_per_path(self):
        # The bridge and the lookup layer both want the dossier. Two objects over one
        # file would each keep their own copy and overwrite the other's writes.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.json"
            self.assertIs(store(path), store(path))

    def test_a_later_caller_without_an_opinion_keeps_the_configured_age(self):
        # The bridge sets max_age_hours from config; the lookup layer asks for the store
        # with no argument. That second call must not reset the first one's setting back
        # to the default, or a configured staleness window silently stops applying.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.json"
            configured = store(path, max_age_hours=12.0)
            self.assertEqual(12.0, store(path).max_age_hours)
            self.assertEqual(12.0, configured.max_age_hours)

    def test_an_explicit_age_still_takes_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.json"
            store(path, max_age_hours=12.0)
            self.assertEqual(3.0, store(path, max_age_hours=3.0).max_age_hours)


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = DossierStore(Path(self.dir.name) / "d.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_an_empty_dossier_says_so(self):
        self.assertIn("No nations read", self.store.summary())

    def test_it_distinguishes_noticed_from_read(self):
        self.store.notice(47, "Rustlung")
        self.assertIn("noticed but not looked at", self.store.summary())

    def test_a_read_nation_is_summarised_with_its_defence(self):
        self.store.record_nation(nation())
        summary = self.store.summary()
        self.assertIn("Rustlung", summary)
        self.assertIn("60 defending", summary)   # the 40 attackers are not defence


class MarketSightingTests(unittest.TestCase):
    """A nation bidding in the market gets noted without a page being spent on it.

    `_notice_market_nations` is exercised directly rather than through a real bridge: the
    bridge needs a config, a monitor and a live session, and none of that is what this is
    about. What matters is that the sighting is read off the snapshot's own MarketOrder
    objects -- which carry `nation_id` as a number -- rather than parsed back out of the
    rendered alert text.
    """

    class Order:
        def __init__(self, nation_id, nation_name, good):
            self.nation_id, self.nation_name, self.good = nation_id, nation_name, good

    class Snapshot:
        def __init__(self, orders):
            self.market_orders = orders

    def setUp(self):
        from core.clop_bridge import ClopBridge

        self.dir = tempfile.TemporaryDirectory()
        self.notice = ClopBridge._notice_market_nations

        class Stub:
            pass

        self.bridge = Stub()
        self.bridge.dossier = DossierStore(Path(self.dir.name) / "d.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_a_bidder_is_noted_for_a_later_look(self):
        self.notice(self.bridge, self.Snapshot([
            self.Order(47, "Rustlung", "Oil"),
        ]))
        self.assertEqual([47], self.bridge.dossier.pending())

    def test_the_sighting_records_the_name_and_the_reason(self):
        self.notice(self.bridge, self.Snapshot([self.Order(47, "Rustlung", "Coffee")]))
        seen = self.bridge.dossier._seen["47"]
        self.assertEqual("Rustlung", seen["name"])
        self.assertIn("Coffee", seen["why"])

    def test_npc_empires_are_skipped(self):
        # Negative ids are the NPC empires. They have no viewnation.php page, so noting
        # them would put an unreadable entry at the front of the queue forever.
        self.notice(self.bridge, self.Snapshot([
            self.Order(-1, "Solar Empire", "Oil"),
            self.Order(47, "Rustlung", "Oil"),
        ]))
        self.assertEqual([47], self.bridge.dossier.pending())

    def test_the_most_persistent_bidder_is_read_first(self):
        for _ in range(3):
            self.notice(self.bridge, self.Snapshot([self.Order(47, "Rustlung", "Oil")]))
        self.notice(self.bridge, self.Snapshot([self.Order(9, "Saltlick", "Pies")]))
        self.assertEqual([47, 9], self.bridge.dossier.pending())

    def test_a_nation_already_read_recently_is_not_queued_again(self):
        self.bridge.dossier.record_nation(nation())
        self.notice(self.bridge, self.Snapshot([self.Order(47, "Rustlung", "Oil")]))
        self.assertEqual([], self.bridge.dossier.pending())

    def test_a_snapshot_with_no_market_orders_is_harmless(self):
        class Bare:
            pass

        self.notice(self.bridge, Bare())
        self.assertEqual([], self.bridge.dossier.pending())


if __name__ == "__main__":
    unittest.main()
