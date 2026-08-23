"""Runs the CLOP monitor's poll loop inside the pet's process.

The monitor lives in ``clop_monitor/`` beside this package. It is a standalone program that
polls the game every 60 seconds and raises Windows toasts, and it still works that way if you
run it directly. Everything platform-specific about it lives behind two methods on its
``Notifier``; ``build_alerts`` itself is pure. So this hands ``check_and_notify`` a sink of our
own: its toasts never happen, and its alerts arrive in the box above the pony instead.

Its ``main()`` is bypassed entirely -- it is one undecomposed function that also owns argument
parsing, credential prompting and the sheet sync. What is reused is the layer below:
``ClopClient``, ``build_alerts`` and ``check_and_notify``.

The monitor goes on ``sys.path`` as a directory rather than being imported as a package,
because its modules import each other by bare name (``from goods import ...``). That is how it
runs as a program, and rewriting every module and all seven of its test files into relative
imports would buy nothing.

Failures here are all soft: the bridge logs, marks itself unavailable, and the pet runs on
without CLOP features. A desktop companion that refuses to start over a missing game monitor
would be a bad trade.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Never poll faster than the monitor's own floor. It talks to someone else's server.
MIN_INTERVAL_S = 15


class ClopUnavailable(RuntimeError):
    """The monitor could not be loaded or logged in. The pet carries on without it."""


def load_monitor(monitor_path: Path):
    """Import the monitor package from its checkout, or say why not.

    Puts the checkout on ``sys.path`` because its modules import each other by bare name
    (``from goods import ...``), which is how it runs as a program and is not worth rewriting
    to make it importable from elsewhere.
    """
    path = Path(monitor_path).expanduser()
    if not path.is_absolute():
        path = (Path(__file__).resolve().parent.parent / path).resolve()
    if not (path / "clop_monitor.py").is_file():
        raise ClopUnavailable(
            f"No clop_monitor.py under {path}. The bundled monitor should be at "
            f"clop_monitor/ beside main.py; check clop.monitor_path, or set "
            f"clop.enabled to false."
        )
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    try:
        # fourchan is imported for its side effect of proving the checkout is complete:
        # clop_monitor pulls it in itself, and a half-updated checkout should fail here
        # with a clear message rather than at the first thread read.
        import importlib

        monitor = importlib.import_module("clop_monitor")
        importlib.import_module("fourchan")
    except Exception as exc:  # pragma: no cover - depends on the checkout
        raise ClopUnavailable(f"Could not import the monitor from {path}: {exc}") from exc
    return monitor, path


class PetSink:
    """Stands in for the monitor's ``Notifier``, duck-typed to the two methods it calls.

    ``notify`` returns False deliberately. The monitor reads a True as "a blocking dialog was
    shown and dismissed", and refreshes its snapshot on the reasoning that whoever dismissed it
    has now read the messages. A box appearing above a pony is not evidence of that, so False is
    the honest answer and matches what the toast path already returns.
    """

    def __init__(
        self,
        monitor,
        on_notification: Callable[[Dict[str, Any]], None],
        unread=None,
        on_failure: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._monitor = monitor
        self._on_notification = on_notification
        self._unread = unread
        self._on_failure = on_failure

    def notify(self, message: str, alerts=None) -> bool:
        if not alerts:
            # sync_sheet_step and a few others hand over a bare string with no Alert objects.
            alerts = [self._monitor.Alert(message)]
        for alert in alerts:
            try:
                payload = self._monitor.alert_parts(alert)
            except Exception as exc:
                logger.warning("Could not render an alert (%s); passing the text through", exc)
                payload = {"title": "CLOP monitor", "body": str(alert),
                           "url": None, "category": "other", "colour": None}
            if self._unread is not None:
                self._unread.add(payload)
            try:
                self._on_notification(payload)
            except Exception as exc:
                logger.warning("Notification delivery failed: %s", exc)
        return False

    def notify_failure(self, message: str) -> bool:
        logger.warning("CLOP monitor: %s", message)
        if self._on_failure is not None:
            try:
                self._on_failure(message)
            except Exception:
                pass
        return False


class ClopBridge:
    """Owns the monitor's client, its poll thread, and the sink alerts arrive through."""

    def __init__(
        self,
        clop_config,
        on_notification: Callable[[Dict[str, Any]], None],
        unread=None,
        on_failure: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.config = clop_config
        self._on_notification = on_notification
        self._unread = unread
        self._on_failure = on_failure

        self.monitor = None
        self.client = None
        self.settings = None
        self.root: Optional[Path] = None
        self.last_error: Optional[str] = None

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._previous = None
        #: Held around every use of the client. The poll thread and any tool call the character
        #: makes share one authenticated session, and its cookie jar is not reentrant.
        self.lock = threading.RLock()

    # ── Availability ──────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self.client is not None

    def _path(self, configured: Optional[str], *default: str) -> Path:
        if configured:
            candidate = Path(configured).expanduser()
            if not candidate.is_absolute() and self.root is not None:
                candidate = self.root / candidate
            return candidate
        assert self.root is not None
        return self.root.joinpath(*default)

    # ── Setup ─────────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Load the monitor, read its settings, and log in. Raises ClopUnavailable."""
        self.monitor, self.root = load_monitor(self.config.monitor_path)
        monitor = self.monitor

        settings_path = self._path(self.config.settings_file, "settings.json")
        env_path = self._path(self.config.env_file, ".env")

        try:
            self.settings = monitor.load_settings(settings_path)
            env = monitor.load_env_file(env_path)
        except monitor.MonitorError as exc:
            raise ClopUnavailable(str(exc)) from exc

        username = os.environ.get("CLOP_USERNAME") or env.get("CLOP_USERNAME")
        password = os.environ.get("CLOP_PASSWORD") or env.get("CLOP_PASSWORD")
        if not username or not password:
            raise ClopUnavailable(
                f"No CLOP credentials. Set CLOP_USERNAME and CLOP_PASSWORD in {env_path} "
                f"or in the environment."
            )

        # Baseline the thread the way the monitor's own startup does, so the first poll does
        # not alert on a post that was already there. Unlike the monitor this is not fatal:
        # a dead thread should cost the thread feature, not the whole advisor.
        initial_post = None
        if self.settings.fourchan_thread is not None:
            try:
                probe = monitor.ClopClient(
                    self.config.base_url, "", "",
                    fourchan_thread=self.settings.fourchan_thread,
                )
                initial_post = probe._latest_fourchan_post()
            except monitor.MonitorError as exc:
                logger.warning("4chan thread unavailable, continuing without it: %s", exc)

        client = monitor.ClopClient(
            self.config.base_url,
            username,
            password,
            fourchan_thread=self.settings.fourchan_thread,
            initial_fourchan_post=initial_post,
        )
        try:
            client.login()
            message = client.market_preflight(monitor.goods_to_watch(self.settings.alerts))
        except monitor.MonitorError as exc:
            raise ClopUnavailable(f"Could not log in to CLOP: {exc}") from exc
        if message:
            logger.info("CLOP market preflight: %s", message)

        self.client = client
        self.last_error = None
        logger.info("CLOP bridge connected to %s", client.base_url)

    # ── Poll loop ─────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Connect and start polling. Returns False (having logged why) if unavailable."""
        try:
            self.connect()
        except ClopUnavailable as exc:
            self.last_error = str(exc)
            logger.warning("CLOP features are off: %s", exc)
            return False

        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="clop-bridge", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        monitor = self.monitor
        sink = PetSink(monitor, self._on_notification, self._unread, self._on_failure)
        interval = max(MIN_INTERVAL_S, int(self.config.poll_interval_s))
        state_path = self._path(self.config.state_file, ".state", "clop-monitor.json")

        if self.settings.cache.persist_to_file:
            try:
                self._previous = monitor.load_snapshot(state_path)
            except Exception as exc:
                logger.debug("No usable CLOP baseline: %s", exc)

        while not self._stop.is_set():
            try:
                self._poll_once(monitor, sink, state_path)
            except monitor.ArchivedThreadError as exc:
                # The thread died. Everything else still works, so drop it and keep polling
                # rather than taking the whole bridge down with it.
                logger.warning("4chan thread archived, dropping it: %s", exc)
                with self.lock:
                    self.client.fourchan_thread = None
            except monitor.AuthenticationError as exc:
                self.last_error = str(exc)
                logger.error("CLOP authentication failed, stopping the bridge: %s", exc)
                sink.notify_failure(f"CLOP login failed: {exc}")
                return
            except monitor.MonitorError as exc:
                # One bad poll is normal: the game restarts, the network blips. Say so once
                # and try again next time round.
                self.last_error = str(exc)
                logger.warning("CLOP poll failed: %s", exc)
            except Exception:
                logger.exception("Unexpected error in the CLOP poll loop")
            self._stop.wait(interval)

    def _poll_once(self, monitor, sink, state_path: Path) -> None:
        with self.lock:
            stockpiles = None
            if monitor.goods_to_watch(self.settings.alerts):
                _html, stockpiles = monitor.read_overview_stockpiles(self.client)
            current, _paused = monitor.check_and_notify(
                self.client,
                self._previous,
                sink,
                state_path,
                self.settings.alerts,
                self.settings.cache.persist_to_file,
                stockpiles,
            )
        self._previous = current
        self.last_error = None

    # ── Read-through helpers for the tool layer ───────────────────────────

    def stockpiles(self) -> Dict[str, int]:
        """Everything the nation holds right now, as ``{good name: amount}``."""
        self._require()
        with self.lock:
            _html, stock = self.monitor.read_overview_stockpiles(self.client)
        return stock.as_dict()

    def overview_html(self) -> str:
        """The raw overview page, for callers that need more than the resources panel."""
        self._require()
        with self.lock:
            html, _stock = self.monitor.read_overview_stockpiles(self.client)
        return html

    def market_orders(self) -> List[Any]:
        """Pending buy orders for the goods the monitor is configured to watch.

        Only watched goods: the buyer's market is queried one good at a time with a CSRF
        token, so "all of them" would be thirty-one POSTs per ask. Which goods are watched
        lives in the monitor's settings.json under market.goods.
        """
        self._require()
        with self.lock:
            roster = (
                self.client._alliance_roster()
                if self.client.alliance_id is not None
                else None
            )
            return list(self.client._market_orders(roster))

    def thread_posts(self) -> List[Any]:
        """Every post in the configured 4chan thread."""
        self._require()
        with self.lock:
            return self.client.fourchan_thread_posts()

    def _require(self) -> None:
        if not self.available:
            raise ClopUnavailable(self.last_error or "The CLOP bridge is not connected")
