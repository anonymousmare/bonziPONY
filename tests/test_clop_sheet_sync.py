#!/usr/bin/env python3
"""The planning-sheet sync, running inside the pet instead of inside the monitor's main().

The whole claim of this feature is a negative one: the shared sheet cannot tell the
difference. So these tests are about the call, not about the cells -- what writes the cells is
``clop_monitor.sync_sheet_step`` either way, and its own suite covers what it puts where.

What can differ, and is therefore what is pinned here: that it is called at all, that it gets
the same six arguments, that it happens *before* alerting and after the shared overview read
(so both consumers use one fetch), that a tab which does not exist turns the sync off instead
of failing every poll, and that nothing is written when it is off.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "clop_monitor"))

from core.clop_bridge import ClopBridge


class FakeSheet:
    def __init__(self, tabs=("LePone(Z)",)):
        self.tabs = tabs

    def require_tab(self, tab):
        if tab not in self.tabs:
            raise FakeSheets.SheetError(f"nation tab {tab!r} does not exist in the shared sheet")


class FakeSheets:
    """Stands in for the monitor's ``sheets`` module, which talks to Google."""

    class SheetError(RuntimeError):
        pass

    nation = "LePone(Z)"
    tabs = ("LePone(Z)",)

    @classmethod
    def install(cls, nation="LePone(Z)", tabs=("LePone(Z)",)):
        cls.nation, cls.tabs = nation, tabs
        module = type(sys)("sheets")
        module.SheetError = cls.SheetError
        module.GoogleSheet = lambda *a, **k: FakeSheet(cls.tabs)

        def nation_from_env(env_path=None):
            if cls.nation is None:
                raise cls.SheetError("CLOP_NATION is not set.")
            return cls.nation

        module.nation_from_env = nation_from_env
        sys.modules["sheets"] = module
        return module


class FakeMonitor:
    """The monitor module, reduced to the four functions the poll calls."""

    class MonitorError(RuntimeError):
        pass

    ArchivedThreadError = type("ArchivedThreadError", (RuntimeError,), {})
    AuthenticationError = type("AuthenticationError", (RuntimeError,), {})

    def __init__(self, watched=True):
        self.watched = watched
        self.calls = []

    def goods_to_watch(self, _alerts):
        return ("Copper",) if self.watched else ()

    def read_overview_stockpiles(self, _client):
        self.calls.append("read_overview")
        return ("<html>overview</html>", {"Copper": 10})

    def sync_sheet_step(self, client, sheet, nation, notifier, overview_html=None, stock=None):
        self.calls.append(("sync", sheet, nation, overview_html, stock))

    def check_and_notify(self, _client, _previous, _sink, _state, _alerts, _persist, stock):
        self.calls.append(("check_and_notify", stock))
        return (Snapshot(), False)

    def alert_parts(self, alert):
        return {"title": "x", "body": str(alert), "url": None,
                "category": "other", "colour": None}

    class Alert(str):
        icon_key = ""


class Snapshot:
    market_orders = ()


class Config:
    monitor_path = "clop_monitor"
    base_url = "https://4clop.org/"
    settings_file = env_file = state_file = None
    poll_interval_s = 60
    dossier_max_age_hours = 6.0
    roster_max_age_hours = 12.0
    sheet_sync = True
    thread_board = "mlp"
    thread_auto_find = True
    thread_url = ""


def bridge(monitor, failures):
    made = ClopBridge(Config(), on_notification=lambda payload: None,
                      on_failure=failures.append)
    made.monitor = monitor
    made.client = object()          # never dereferenced; the fake monitor takes it and stops
    made.root = ROOT / "clop_monitor"
    return made


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.failures = []
        self.monitor = FakeMonitor()
        self.bridge = bridge(self.monitor, self.failures)
        self.sink = _sink(self.monitor, self.failures)

    def test_a_named_tab_turns_it_on(self):
        FakeSheets.install()
        self.bridge._start_sheet_sync(self.sink)
        self.assertEqual(self.bridge._sheet_nation, "LePone(Z)")
        self.assertEqual(self.failures, [])

    def test_an_unset_nation_stays_off_and_stays_quiet(self):
        # main() raises a dialog here. Most people running a desktop pony have never heard of
        # the sheet, so an unset nation is not an alert -- but it must still be off.
        FakeSheets.install(nation=None)
        self.bridge._start_sheet_sync(self.sink)
        self.assertIsNone(self.bridge._sheet)
        self.assertEqual(self.failures, [])

    def test_a_tab_that_does_not_exist_is_reported_and_turned_off(self):
        # This one is worth waking somebody for: a nation *is* configured, so a tab is
        # expected to be updating and silently is not.
        FakeSheets.install(nation="Nowhere(Q)")
        self.bridge._start_sheet_sync(self.sink)
        self.assertIsNone(self.bridge._sheet)
        self.assertEqual(len(self.failures), 1)
        self.assertIn("Sheet sync is off", self.failures[0])

    def test_the_config_switch_turns_it_off_outright(self):
        FakeSheets.install()
        self.bridge.config.sheet_sync = False
        self.bridge._start_sheet_sync(self.sink)
        self.assertIsNone(self.bridge._sheet)


class PollTests(unittest.TestCase):
    def setUp(self):
        FakeSheets.install()
        self.failures = []
        self.monitor = FakeMonitor()
        self.bridge = bridge(self.monitor, self.failures)
        self.sink = _sink(self.monitor, self.failures)
        self.bridge.settings = _settings()

    def poll(self):
        self.bridge._poll_once(self.monitor, self.sink, Path("state.json"))

    def test_the_sync_runs_before_the_alerting(self):
        # The monitor's order, and not an arbitrary one: the sheet is written from the state
        # the poll observed, ahead of anything that might block on a dialog.
        self.bridge._start_sheet_sync(self.sink)
        self.poll()
        kinds = [c[0] if isinstance(c, tuple) else c for c in self.monitor.calls]
        self.assertEqual(kinds, ["read_overview", "sync", "check_and_notify"])

    def test_it_is_handed_the_page_the_poll_already_fetched(self):
        # One fetch, two consumers. Handing it None would make sync_sheet_step read
        # overview.php a second time every minute.
        self.bridge._start_sheet_sync(self.sink)
        self.poll()
        _kind, sheet, nation, html, stock = self.monitor.calls[1]
        self.assertEqual(nation, "LePone(Z)")
        self.assertEqual(html, "<html>overview</html>")
        self.assertEqual(stock, {"Copper": 10})
        self.assertIsNotNone(sheet)

    def test_with_no_watched_market_it_is_left_to_read_the_page_itself(self):
        # Exactly what main() passes in that case: None, None. sync_sheet_step then fetches.
        self.monitor.watched = False
        self.bridge._start_sheet_sync(self.sink)
        self.poll()
        _kind, _sheet, _nation, html, stock = self.monitor.calls[0]
        self.assertIsNone(html)
        self.assertIsNone(stock)

    def test_nothing_is_written_when_the_sync_is_off(self):
        # No _start_sheet_sync call at all: the poll must be exactly what it was before.
        self.poll()
        kinds = [c[0] if isinstance(c, tuple) else c for c in self.monitor.calls]
        self.assertEqual(kinds, ["read_overview", "check_and_notify"])


def _sink(monitor, failures):
    from core.clop_bridge import PetSink

    return PetSink(monitor, lambda payload: None, None, failures.append, None)


def _settings():
    class Cache:
        persist_to_file = False

    class Settings:
        alerts = object()
        cache = Cache()
        fourchan_thread = None

    return Settings()


if __name__ == "__main__":
    unittest.main()
