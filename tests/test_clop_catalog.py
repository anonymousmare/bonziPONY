#!/usr/bin/env python3
"""Finding the current 4CLOP thread on /mlp/.

The bug this exists for: a thread URL written into a settings file goes stale within a day
or two, because that is how long a 4chan thread lasts. When it does, `[LOOKUP:thread]`
answers "no 4chan thread is configured" -- which the user reads as "she cannot check the
thread", not as "the thread moved". A fresh clone has no settings file at all, so that was
the state out of the box.

The fixture is a real /mlp/ catalog response, trimmed. It keeps the awkward case on
purpose: a FimFiction thread whose opening post says "clop" repeatedly and means pony
pornography by it. Picking that one and reading it out as the game's politics would be a
considerably worse failure than finding nothing.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.clop_catalog import (
    MIN_SCORE, CatalogThread, ThreadResolver, parse_catalog, pick_thread, score_thread,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mlp_catalog.json"

#: The real general in the fixture, and the thread that must never be mistaken for it.
GENERAL = 43454282
CLOPFIC = 43449471


def catalog():
    return parse_catalog(json.loads(FIXTURE.read_text(encoding="utf-8")))


class ParsingTests(unittest.TestCase):
    def test_it_walks_every_page(self):
        # The catalog is a list of pages, not a list of threads. Reading only the first
        # page would miss the general as soon as it slid off page 1.
        self.assertEqual(5, len(catalog()))

    def test_html_is_flattened_to_text(self):
        general = next(t for t in catalog() if t.number == GENERAL)
        self.assertNotIn("<br>", general.body)
        self.assertNotIn("&gt;", general.body)
        self.assertIn(">CLOP has risen", general.body)

    def test_junk_rows_are_skipped_rather_than_raising(self):
        self.assertEqual([], parse_catalog({"not": "a list"}))
        self.assertEqual([], parse_catalog([{"threads": [{"no": "not-a-number"}]}]))
        self.assertEqual([], parse_catalog([{"threads": [{"sub": "no number at all"}]}]))

    def test_a_thread_knows_its_own_url(self):
        general = next(t for t in catalog() if t.number == GENERAL)
        self.assertEqual(f"https://boards.4chan.org/mlp/thread/{GENERAL}",
                         general.url("mlp"))


class ScoringTests(unittest.TestCase):
    def test_the_real_general_is_picked(self):
        self.assertEqual(GENERAL, pick_thread(catalog()).number)

    def test_a_thread_that_merely_says_clop_a_lot_is_not_picked(self):
        rows = {t.number: score_thread(t) for t in catalog()}
        self.assertGreater(rows[GENERAL], rows[CLOPFIC])
        self.assertLess(rows[CLOPFIC], MIN_SCORE)

    def test_the_game_link_alone_is_enough(self):
        # The one signal /mlp/'s ordinary use of the word cannot produce.
        bare = CatalogThread(number=1, subject="untitled",
                             body="playing at https://4clop.org/ tonight")
        self.assertGreaterEqual(score_thread(bare), MIN_SCORE)

    def test_clop_as_part_of_another_word_does_not_count(self):
        self.assertEqual(0, score_thread(
            CatalogThread(number=1, subject="clopfic recommendations",
                          body="post your favourite clopfics and clopping music")))

    def test_nothing_is_picked_when_nothing_convinces(self):
        self.assertIsNone(pick_thread([
            CatalogThread(number=1, subject="RGRE", body="reversed gender roles"),
            CatalogThread(number=2, subject="clopfic thread", body="clopping"),
        ]))

    def test_the_busier_thread_wins_a_tie(self):
        # A new general goes up while the old one is still open; she wants the live one.
        old = CatalogThread(number=1, subject=">CLOP: old edition",
                            body="https://4clop.org/", replies=3)
        new = CatalogThread(number=2, subject=">CLOP: new edition",
                            body="https://4clop.org/", replies=250)
        self.assertEqual(2, pick_thread([old, new]).number)

    def test_the_domain_comes_from_config_not_a_constant(self):
        # base_url is configurable, so a test server's thread should be findable too.
        other = CatalogThread(number=1, subject="general",
                              body="play at https://clop.example.test/")
        self.assertGreaterEqual(score_thread(other, "clop.example.test"), MIN_SCORE)
        self.assertLess(score_thread(other, "4clop.org"), MIN_SCORE)


class ResolverTests(unittest.TestCase):
    """The resolver caches, so a question does not cost a catalog fetch every time."""

    def setUp(self):
        self.calls = []
        self.general = next(t for t in catalog() if t.number == GENERAL)

    def _resolver(self, answers):
        resolver = ThreadResolver()
        supply = list(answers)

        def fake(board, domain, timeout=0):
            self.calls.append(board)
            return supply.pop(0) if supply else None

        import core.clop_catalog as module
        original = module.find_thread
        module.find_thread = fake
        self.addCleanup(setattr, module, "find_thread", original)
        return resolver

    def test_the_answer_is_reused_rather_than_refetched(self):
        resolver = self._resolver([self.general, self.general])
        self.assertEqual(GENERAL, resolver.resolve().number)
        self.assertEqual(GENERAL, resolver.resolve().number)
        self.assertEqual(1, len(self.calls))

    def test_force_goes_back_to_the_board(self):
        resolver = self._resolver([self.general, self.general])
        resolver.resolve()
        resolver.resolve(force=True)
        self.assertEqual(2, len(self.calls))

    def test_forgetting_sends_the_next_resolve_back_to_the_board(self):
        # What an archived thread looks like from here: it stops returning posts.
        resolver = self._resolver([self.general, self.general])
        resolver.resolve()
        resolver.forget()
        self.assertIsNone(resolver.current)
        resolver.resolve()
        self.assertEqual(2, len(self.calls))

    def test_a_failed_search_keeps_the_thread_it_already_had(self):
        # A board hiccup should not cost her a thread she was reading happily.
        resolver = self._resolver([self.general, None])
        resolver.resolve()
        self.assertEqual(GENERAL, resolver.resolve(force=True).number)

    def test_it_returns_none_when_it_never_found_anything(self):
        self.assertIsNone(self._resolver([None]).resolve())


class BridgeThreadTests(unittest.TestCase):
    """What the user actually hit: `[LOOKUP:thread]` on a clone with no settings file."""

    def setUp(self):
        import threading

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "clop_monitor"))
        import clop_monitor as monitor

        from core.clop_bridge import ClopBridge
        from core.config_loader import ClopConfig

        self.general = next(t for t in catalog() if t.number == GENERAL)
        self.fetches = []

        outer = self

        class Client:
            fourchan_thread = None

            def fourchan_thread_posts(self):
                outer.fetches.append(self.fourchan_thread)
                if self.fourchan_thread is None:
                    return []
                return outer.posts_for(self.fourchan_thread.thread_id)

        self.bridge = ClopBridge.__new__(ClopBridge)
        self.bridge.config = ClopConfig()
        self.bridge.monitor = monitor
        self.bridge.client = Client()
        self.bridge.lock = threading.Lock()
        self.bridge._thread_resolver = ThreadResolver()

        # A connected bridge, without standing up a real session.
        self.bridge._require = lambda: None

        import core.clop_catalog as module
        original = module.find_thread
        module.find_thread = lambda *a, **k: self.found
        self.addCleanup(setattr, module, "find_thread", original)
        self.found = self.general

    def posts_for(self, thread_id):
        return ["a post"] if thread_id == GENERAL else []

    def test_it_finds_a_thread_when_none_is_configured(self):
        # The out-of-the-box state: no settings.json, so no thread_url.
        self.assertIsNone(self.bridge.client.fourchan_thread)
        self.assertEqual(["a post"], self.bridge.thread_posts())
        self.assertEqual(GENERAL, self.bridge.client.fourchan_thread.thread_id)

    def test_it_says_what_it_is_reading(self):
        self.bridge.thread_posts()
        self.assertIn(str(GENERAL), self.bridge.thread_description())

    def test_an_archived_thread_is_replaced_rather_than_reported_empty(self):
        # A configured thread that returns nothing is what archiving looks like from here.
        self.bridge.client.fourchan_thread = \
            self.bridge.monitor.parse_fourchan_thread_url(
                "https://boards.4chan.org/mlp/thread/1")
        self.assertEqual(["a post"], self.bridge.thread_posts())
        self.assertEqual(GENERAL, self.bridge.client.fourchan_thread.thread_id)

    def test_auto_find_off_means_it_never_goes_looking(self):
        self.bridge.config.thread_auto_find = False
        self.assertEqual([], self.bridge.thread_posts())
        self.assertIsNone(self.bridge.client.fourchan_thread)

    def test_finding_nothing_is_an_empty_read_not_a_crash(self):
        self.found = None
        self.assertEqual([], self.bridge.thread_posts())


if __name__ == "__main__":
    unittest.main()
