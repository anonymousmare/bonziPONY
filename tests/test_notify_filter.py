"""What reaches the notification box, and what the mute button switches off.

The GUI half of this cannot be tested here -- PyQt is not a test dependency and the box is a
top-level window -- so what is pinned is the part that decides: ``allows``, the two grains of
muting, and the fallback that reads a good out of an old payload's title.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.notify_filter import (
    CATEGORY_KEYS,
    NotifyFilter,
    category_label,
    category_noun,
    subject_of,
)


def market(subject: str = "Copper", title: str = None) -> dict:
    """A market alert as ``alert_parts`` renders one, plus the subject PetSink adds."""
    return {
        "title": title if title is not None else f"Buy orders for {subject}",
        "body": "someone wants 5 @ 12",
        "url": None,
        "category": "market",
        "colour": "#b87333",
        "subject": subject,
    }


def news() -> dict:
    return {"title": "News", "body": "the war goes on", "url": None,
            "category": "news", "colour": None, "subject": ""}


class FilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "notify_filters.json"

    def filter(self) -> NotifyFilter:
        return NotifyFilter(self.path)

    def test_everything_allowed_by_default(self):
        nf = self.filter()
        self.assertTrue(nf.allows(market()))
        self.assertTrue(nf.allows(news()))
        self.assertEqual(nf.summary(), "showing everything")

    def test_muting_a_good_leaves_other_goods_alone(self):
        nf = self.filter()
        nf.mute_subject("Copper")
        self.assertFalse(nf.allows(market("Copper")))
        self.assertTrue(nf.allows(market("Oil")))
        # And not the whole category: a market alert about something else still arrives.
        self.assertTrue(nf.category_enabled("market"))

    def test_muting_a_good_is_case_insensitive(self):
        nf = self.filter()
        nf.mute_subject("copper")
        self.assertFalse(nf.allows(market("Copper")))
        self.assertFalse(nf.allows(market("COPPER")))

    def test_muting_a_kind(self):
        nf = self.filter()
        nf.set_category("news", False)
        self.assertFalse(nf.allows(news()))
        self.assertTrue(nf.allows(market()))

    def test_mute_prefers_the_good_over_the_kind(self):
        """"Mute Copper" must not silence every market alert there will ever be."""
        nf = self.filter()
        kind, value, label = nf.mute_target(market("Copper"))
        self.assertEqual((kind, value), ("subject", "Copper"))
        self.assertEqual(label, "Mute Copper")
        self.assertEqual(nf.mute(market("Copper")), "Copper")
        self.assertTrue(nf.category_enabled("market"))
        self.assertTrue(nf.allows(market("Oil")))

    def test_mute_falls_back_to_the_kind_when_there_is_no_good(self):
        nf = self.filter()
        kind, value, label = nf.mute_target(news())
        self.assertEqual((kind, value), ("category", "news"))
        self.assertEqual(label, "Mute news updates")
        nf.mute(news())
        self.assertFalse(nf.allows(news()))

    def test_subject_is_read_from_the_title_when_the_payload_predates_it(self):
        """A payload restored from clop_unread.json can be older than the ``subject`` key."""
        stored = market("Copper")
        stored.pop("subject")
        self.assertEqual(subject_of(stored), "Copper")
        nf = self.filter()
        nf.mute_subject("Copper")
        self.assertFalse(nf.allows(stored))

    def test_only_market_alerts_have_a_subject(self):
        """Muting copper silences the order lists, not a battle report that mentions it."""
        report = {"title": "Report: raid for Copper", "body": "", "url": None,
                  "category": "report", "colour": "#b87333"}
        self.assertEqual(subject_of(report), "")
        nf = self.filter()
        nf.mute_subject("Copper")
        self.assertTrue(nf.allows(report))

    def test_persists_and_reloads(self):
        nf = self.filter()
        nf.mute_subject("Copper")
        nf.set_category("news", False)

        reloaded = self.filter()
        self.assertFalse(reloaded.allows(market("Copper")))
        self.assertFalse(reloaded.allows(news()))
        self.assertEqual(reloaded.muted_subjects(), ["Copper"])
        self.assertEqual(reloaded.muted_categories(), ["news"])

    def test_unmuting(self):
        nf = self.filter()
        nf.mute_subject("Copper")
        nf.unmute_subject("copper")
        self.assertTrue(nf.allows(market("Copper")))
        self.assertEqual(nf.muted_subjects(), [])

    def test_unmute_all(self):
        nf = self.filter()
        nf.mute_subject("Copper")
        nf.set_category("market", False)
        nf.unmute_all()
        self.assertTrue(nf.allows(market("Copper")))
        self.assertEqual(self.filter().summary(), "showing everything")

    def test_unreadable_file_is_not_fatal(self):
        self.path.write_text("{not json", encoding="utf-8")
        nf = self.filter()
        self.assertTrue(nf.allows(market()))

    def test_junk_entries_are_ignored(self):
        self.path.write_text(json.dumps({
            "categories": {"news": False, "market": "yes"},   # a string is not a toggle
            "subjects": ["Copper", "", 7],
        }), encoding="utf-8")
        nf = self.filter()
        self.assertFalse(nf.allows(news()))
        self.assertTrue(nf.category_enabled("market"))
        self.assertEqual(nf.muted_subjects(), ["Copper"])

    def test_an_alert_with_no_category_is_treated_as_other(self):
        nf = self.filter()
        bare = {"title": "CLOP monitor", "body": "sheet synced"}
        self.assertTrue(nf.allows(bare))
        nf.set_category("other", False)
        self.assertFalse(nf.allows(bare))

    def test_every_category_has_a_label_and_a_noun(self):
        for key in CATEGORY_KEYS:
            self.assertTrue(category_label(key))
            self.assertTrue(category_noun(key))

    def test_summary_names_what_is_off(self):
        nf = self.filter()
        nf.set_category("news", False)
        nf.mute_subject("Copper")
        summary = nf.summary()
        self.assertIn("news", summary)
        self.assertIn("Copper", summary)


class CategoryCoverageTests(unittest.TestCase):
    """The keys here must be the ones the monitor actually emits, or a toggle does nothing."""

    def test_keys_match_the_monitors_categories(self):
        import sys

        root = Path(__file__).resolve().parent.parent / "clop_monitor"
        if not (root / "clop_monitor.py").is_file():   # pragma: no cover - partial checkout
            self.skipTest("the monitor is not in this checkout")
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            import clop_monitor as monitor
        except Exception as exc:                        # pragma: no cover - optional deps
            self.skipTest(f"the monitor does not import here: {exc}")

        expected = set(monitor.CATEGORY_ICON_KEYS) | {
            monitor.ALERT_CATEGORY_MARKET, monitor.ALERT_CATEGORY_OTHER,
        }
        self.assertEqual(set(CATEGORY_KEYS), expected)


if __name__ == "__main__":
    unittest.main()
