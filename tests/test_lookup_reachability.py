#!/usr/bin/env python3
"""Every lookup she is told about has to actually run.

This file exists because the same bug happened twice. Tools were written, registered in one
place, and never connected to the thing that calls them -- first `ToolRegistry.dispatch`
itself, which nothing invoked, then nine live tools that had no `LOOKUPS` row. Both times the
code compiled, imported and looked finished.

So these are structural tests rather than behavioural ones. They do not check what a lookup
says; they check that saying anything is possible. If someone adds a tool and forgets the
wiring, this fails immediately instead of at the moment she tries to use it.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "clop_monitor"))

from core.clop_tools import LOOKUPS, ToolRegistry, make_live_tools


class FakeBridge:
    """A bridge that is connected but whose fetches are never reached.

    Only the wiring is under test here: `_callable` has to resolve every live row to a real
    function. Whether that function then works is the job of the parser tests.
    """

    available = True

    class _Config:
        read_alliance_messages = True

    config = _Config()

    def __getattr__(self, name):
        def _unused(*args, **kwargs):
            raise AssertionError(f"{name} should not have been called")
        return _unused


class ReachabilityTests(unittest.TestCase):
    def setUp(self):
        self.connected = ToolRegistry(FakeBridge())
        self.offline = ToolRegistry(None)

    def test_every_live_row_resolves_to_a_callable(self):
        """The bug this file exists for: a row whose live_name matches no tool."""
        for lookup in LOOKUPS:
            if not lookup.live:
                continue
            with self.subTest(lookup=lookup.name):
                self.assertTrue(lookup.live_name,
                                f"{lookup.name} is live but names no callable")
                self.assertIsNotNone(
                    self.connected._callable(lookup),
                    f"{lookup.name} names {lookup.live_name!r}, which make_live_tools "
                    f"does not provide",
                )

    def test_every_static_row_resolves_to_a_callable(self):
        for lookup in LOOKUPS:
            if lookup.live:
                continue
            with self.subTest(lookup=lookup.name):
                self.assertIsNotNone(self.offline._callable(lookup))

    def test_every_live_tool_has_a_row(self):
        """The other direction: a tool nobody can ask for is dead code."""
        rows = {l.live_name for l in LOOKUPS if l.live}
        built = set(make_live_tools(FakeBridge()))
        self.assertEqual(sorted(built - rows), [],
                         "these tools exist but no [LOOKUP:] can reach them")

    def test_no_row_is_reachable_by_two_names(self):
        seen = {}
        for lookup in LOOKUPS:
            for name in (lookup.name,) + lookup.aliases:
                self.assertNotIn(name, seen,
                                 f"{name!r} is claimed by both {seen.get(name)} and {lookup.name}")
                seen[name] = lookup.name

    def test_she_is_told_about_everything_she_can_reach(self):
        """A lookup missing from the prompt block may as well not exist."""
        block = self.connected.prompt_block()
        for lookup in self.connected.available:
            with self.subTest(lookup=lookup.name):
                self.assertIn(f"[LOOKUP:{lookup.name}", block)

    def test_offline_offers_only_what_works(self):
        offered = set(self.offline.names)
        live = {l.name for l in LOOKUPS if l.live}
        self.assertEqual(offered & live, set(),
                         "a live lookup is being offered with no game connection")
        self.assertIn("dossier", offered,
                      "the dossier is a file and should answer with the game down")

    def test_a_live_lookup_asked_for_offline_explains_itself(self):
        answer = self.offline.dispatch("stockpiles")
        self.assertIn("error:", answer)
        self.assertIn("live game", answer)

    def test_alliance_chat_is_withheld_when_the_flag_is_off(self):
        class Quiet(FakeBridge):
            class _Config:
                read_alliance_messages = False
            config = _Config()

        registry = ToolRegistry(Quiet())
        self.assertNotIn("alliance_messages", registry.names)
        self.assertNotIn("[LOOKUP:alliance_messages", registry.prompt_block())

    def test_alliance_chat_is_offered_when_the_flag_is_on(self):
        self.assertIn("alliance_messages", self.connected.names)


class RenderingTests(unittest.TestCase):
    """Routing is not enough: the renderer has to match the shape it is handed.

    `FakeBridge` above never lets a fetch return, which is what let `get_nation_status`
    call `.display()` on `NationStatus.government` -- a plain `str` -- for as long as it
    did. Reachable and correct are two claims, so this makes the second one separately.
    """

    def test_nation_status_renders_the_real_dataclass(self):
        import test_nation  # the monitor's own overview sample, rendered as the game does

        html = test_nation.panel()

        class Bridge:
            available = True

            class _Config:
                read_alliance_messages = False

            config = _Config()

            def overview_html(self):
                return html

        answer = ToolRegistry(Bridge()).dispatch("status")
        self.assertNotIn("error:", answer)
        # One field of each kind: a bare str, a Reading, and a bare int.
        self.assertIn("Government: Loose Despotism", answer)
        self.assertRegex(answer, r"Satisfaction: \d+ \(-?\d+\)")
        self.assertIn("GDP last turn: 60,900 bits per tick", answer)
        self.assertIn("Funds: 1,234,567 bits", answer)


if __name__ == "__main__":
    unittest.main()
