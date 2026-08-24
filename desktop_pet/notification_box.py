"""The box above the pony where relayed notifications land.

Painted from ``panel_style``, the palette the speech bubble and the heard-text overlay also
use, with four differences that matter:

* **It is clickable.** ``Qt.WindowTransparentForInput`` is deliberately absent. That flag is
  what makes the other overlays pass clicks through to whatever is underneath, and it is the
  one thing a notification with a button cannot have.
* **It dodges.** It wants to sit above the pony, but she is not the only thing up there --
  when she is talking, her bubble is. ``set_avoid_widgets`` names the windows it must not
  cover and ``_reposition`` stacks above whichever of them are on screen right now.
* **It has a 2px coloured trim** along the top when the alert is about a particular good, and
  no trim at all when it is not. See ``good_colours`` in the monitor for which is which.
* **It never hides itself.** A notification carrying a "mark as read" button should wait to be
  read. The auto-hide timer the other overlays use is gone.

Two buttons, because two things are worth doing with an alert you are looking at: mark it read,
or say you never want this kind again. The second one writes to a ``NotifyFilter`` -- the
alternative was finding ``market.goods`` in the monitor's ``settings.json``, which is not a
thing anyone does at the moment they are annoyed by a notification. Clicking anywhere else on
an alert that carries a link opens it and clears it in one go: following the link is reading
it, and leaving the panel behind would mean tidying up after every link you follow.

Notifications arrive in bursts -- the monitor raises up to six in one poll -- so this shows one
at a time with a count of what is behind it, rather than stacking six always-on-top windows in
the corner of the screen.

Payloads are plain dicts, the shape ``clop_monitor.alert_parts`` returns, so the GUI layer never
imports the monitor's types.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from PyQt5.QtCore import QPoint, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
)
from PyQt5.QtWidgets import QApplication, QWidget

from desktop_pet import stacking
from desktop_pet.panel_style import (
    BUTTON_BG,
    BUTTON_BG_HOVER,
    BUTTON_RADIUS,
    BUTTON_TEXT,
    PANEL_BORDER,
    TEXT_BODY,
    TEXT_BRIGHT,
    TEXT_DIM,
    panel_path,
    paint_panel,
)
from desktop_pet.typewriter_sound import TypewriterSound

logger = logging.getLogger(__name__)

_MAX_WIDTH = 340
_PADDING = 10
_TRIM_HEIGHT = 2          # the coloured strip along the top
_MAX_BODY_LINES = 4       # "not too large" -- a long report is clipped, not scrolled
_BUTTON_HEIGHT = 20
_BUTTON_PAD = 8
_BUTTON_GAP = 6

_MARK_READ_LABEL = "Mark as read"
#: Clicks closer together than this share one burst. Six alerts from one poll are six pushes.
_POP_SOUND_INTERVAL_S = 0.6


class NotificationBox(QWidget):
    """One notification at a time, above the pony, with the rest queued behind it."""

    #: Emitted with the payload when the reader marks one read.
    dismissed = pyqtSignal(object)
    #: Emitted with the payload when the reader clicks through to its link.
    opened = pyqtSignal(object)
    #: Emitted when the last queued notification has been cleared.
    emptied = pyqtSignal()
    #: Emitted with a description of what was muted, when the reader mutes from the box.
    muted = pyqtSignal(str)

    def __init__(self, notify_filter=None) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            # Qt.WindowTransparentForInput is deliberately NOT set: it is what makes
            # the other overlays click-through, and this one has to be clickable.
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)

        self._queue: List[Dict[str, Any]] = []
        self._anchor_widget: Optional[QWidget] = None
        #: Windows this must not cover: her speech bubble, and anything else that lands
        #: in the same strip of screen. See ``set_avoid_widgets``.
        self._avoid: List[QWidget] = []
        self._button_rects: Dict[str, QRect] = {}
        self._hover: Optional[str] = None
        #: The muting the "Mute ..." button writes to. None means no button.
        self.filter = notify_filter

        self._title_font = QFont("Segoe UI", 9)
        self._title_font.setBold(True)
        self._title_font.setStyleStrategy(QFont.PreferAntialias)
        self._body_font = QFont("Segoe UI", 9)
        self._body_font.setStyleStrategy(QFont.PreferAntialias)
        self._small_font = QFont("Segoe UI", 8)
        self._small_font.setStyleStrategy(QFont.PreferAntialias)

        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(33)
        self._follow_timer.timeout.connect(self._follow_tick)

        self._sound = TypewriterSound()
        self._last_pop_sound = 0.0

        # The Qt always-on-top hint alone loses to browsers, same as PetWindow finds.
        self._topmost_counter = 0

    # ── Wiring ────────────────────────────────────────────────────────────

    def set_anchor_widget(self, widget: QWidget) -> None:
        self._anchor_widget = widget

    def set_avoid_widgets(self, widgets) -> None:
        """Name the windows this box must not sit on top of.

        Checked live, on their visibility, every follow tick: a bubble that appears while a
        notification is up pushes the notification further up, and the notification drops back
        when the bubble goes away.
        """
        self._avoid = [w for w in (widgets or []) if w is not None]

    def set_typewriter_sound(self, enabled: bool) -> None:
        """Same toggle the speech bubble uses, so one setting governs both."""
        self._sound.set_enabled(enabled)

    def set_filter(self, notify_filter) -> None:
        self.filter = notify_filter
        self.update()

    @property
    def current(self) -> Optional[Dict[str, Any]]:
        return self._queue[0] if self._queue else None

    @property
    def pending(self) -> int:
        return len(self._queue)

    # ── Queue ─────────────────────────────────────────────────────────────

    def push(self, payload: Dict[str, Any]) -> None:
        """Show a notification, putting anything already showing behind it.

        Newest first: what just happened is what the reader most likely wants, and the
        older ones are still counted and still reachable by working through them.
        """
        if not isinstance(payload, dict):
            logger.warning("Ignoring notification payload of type %s", type(payload).__name__)
            return
        # The sink filters before this, but a payload restored from a previous run predates
        # whatever has been muted since, and would otherwise come back on the next restart.
        if self.filter is not None and not self.filter.allows(payload):
            logger.debug("Filtered out a %s notification", payload.get("category"))
            return
        self._queue.insert(0, payload)
        self._show_current(pop=True)

    def push_many(self, payloads) -> None:
        for payload in payloads:
            self.push(payload)

    def dismiss_current(self) -> None:
        """Mark the shown notification read and move to the next."""
        if not self._queue:
            return
        payload = self._queue.pop(0)
        self.dismissed.emit(payload)
        if self._queue:
            self._show_current()
        else:
            self._hide()
            self.emptied.emit()

    def mute_current(self) -> None:
        """Silence this kind of alert, and drop everything queued that it silences.

        Marking those read rather than dropping them silently is the honest bookkeeping: they
        are gone from the catch-up too, which is the point of muting them.
        """
        payload = self.current
        if payload is None or self.filter is None:
            return
        try:
            what = self.filter.mute(payload)
        except Exception as exc:
            logger.warning("Could not mute %s: %s", payload.get("title"), exc)
            return
        logger.info("Muted %s from the notification box", what)
        self.muted.emit(str(what))

        remaining: List[Dict[str, Any]] = []
        for item in self._queue:
            if self.filter.allows(item):
                remaining.append(item)
            else:
                self.dismissed.emit(item)
        self._queue = remaining
        if self._queue:
            self._show_current()
        else:
            self._hide()
            self.emptied.emit()

    def clear(self) -> None:
        """Drop everything and hide. Does not emit `dismissed` for what is dropped."""
        had_any = bool(self._queue)
        self._queue.clear()
        self._hide()
        if had_any:
            self.emptied.emit()

    def _hide(self) -> None:
        self._follow_timer.stop()
        self.hide()

    # ── Presentation ──────────────────────────────────────────────────────

    def _show_current(self, pop: bool = False) -> None:
        if not self._queue:
            self.clear()
            return
        self._hover = None
        self._resize_and_position()
        self.show()
        self.raise_()
        self._ensure_topmost()
        if pop:
            self._play_pop()
        if self._anchor_widget is not None:
            self._follow_timer.start()
        self.update()

    def _play_pop(self) -> None:
        """A short typewriter burst, once per burst of arrivals rather than once each."""
        now = time.monotonic()
        if now - self._last_pop_sound < _POP_SOUND_INTERVAL_S:
            return
        self._last_pop_sound = now
        self._sound.burst()

    def _body_lines(self, text: str, width: int) -> List[str]:
        """Wrap the body to the panel width, clipped to _MAX_BODY_LINES with an ellipsis."""
        if not text:
            return []
        fm = QFontMetrics(self._body_font)
        lines: List[str] = []
        for paragraph in text.splitlines():
            if not paragraph.strip():
                continue
            words = paragraph.split()
            line = ""
            for word in words:
                candidate = f"{line} {word}".strip()
                if fm.horizontalAdvance(candidate) <= width or not line:
                    line = candidate
                else:
                    lines.append(line)
                    line = word
            if line:
                lines.append(line)

        if len(lines) > _MAX_BODY_LINES:
            lines = lines[:_MAX_BODY_LINES]
            lines[-1] = fm.elidedText(lines[-1] + " ...", Qt.ElideRight, width)
        return lines

    def _buttons(self) -> List[Tuple[str, str]]:
        """The (name, label) pairs on the button row, left to right."""
        buttons = [("read", _MARK_READ_LABEL)]
        payload = self.current
        if payload is not None and self.filter is not None:
            try:
                buttons.append(("mute", self.filter.mute_target(payload)[2]))
            except Exception as exc:      # a broken filter must not blank the box
                logger.debug("No mute button for this alert: %s", exc)
        return buttons

    def _notes(self) -> List[str]:
        """The dim right-hand hint, longest form first: what is behind this, and whether
        clicking goes anywhere. The caller drops parts that do not fit."""
        notes = []
        if len(self._queue) > 1:
            notes.append(f"{len(self._queue) - 1} more")
        if (self.current or {}).get("url"):
            notes.append("click to open")
        return notes

    def _notes_text(self, available: float) -> str:
        """As much of the hint as fits, dropping the least useful part first.

        "1 more" is what the reader has to act on; "click to open" is a nicety. Half of
        "click to op..." is worse than neither.
        """
        parts = self._notes()
        fm = QFontMetrics(self._small_font)
        while parts:
            text = " · ".join(parts)
            if fm.horizontalAdvance(text) <= available:
                return text
            parts.pop()
        return ""

    def _layout(self):
        """Panel width plus the y offsets everything is drawn at."""
        payload = self.current or {}
        inner = _MAX_WIDTH - 2 * _PADDING

        title_fm = QFontMetrics(self._title_font)
        body_fm = QFontMetrics(self._body_font)
        small_fm = QFontMetrics(self._small_font)

        title = str(payload.get("title") or "")
        lines = self._body_lines(str(payload.get("body") or ""), inner)

        widest = title_fm.horizontalAdvance(title)
        for line in lines:
            widest = max(widest, body_fm.horizontalAdvance(line))

        # The button row has to fit too, or "Mute Coffee Beans" runs under the "2 more".
        buttons = self._buttons()
        row = sum(small_fm.horizontalAdvance(label) + 2 * _BUTTON_PAD for _, label in buttons)
        row += _BUTTON_GAP * (len(buttons) - 1)
        notes = " · ".join(self._notes())
        if notes:
            # +2 so a hint that measures exactly the space it is given is not elided by a
            # rounding pixel, which is how "click to open" became "click to op...".
            row += _BUTTON_GAP + small_fm.horizontalAdvance(notes) + 2
        widest = max(widest, row)

        width = min(max(widest + 2 * _PADDING, 180), _MAX_WIDTH)

        y = _TRIM_HEIGHT + _PADDING
        title_y = y
        y += title_fm.height() + (4 if lines else 0)
        body_y = y
        y += len(lines) * body_fm.lineSpacing()
        y += _PADDING
        button_y = y
        y += _BUTTON_HEIGHT + _PADDING

        return width, int(y), title_y, body_y, button_y, lines

    def _resize_and_position(self) -> None:
        width, height, *_ = self._layout()
        self.setFixedSize(width, height)
        self._reposition()

    # ── Where it sits ─────────────────────────────────────────────────────

    def _obstacles(self) -> List[Tuple[int, int, int, int]]:
        """The rectangles this must not cover: the pony, plus whatever else is up there."""
        return stacking.visible_rects([self._anchor_widget] + list(self._avoid))

    def _reposition(self) -> None:
        """Sit above the pony, above her bubble if she has one, clamped to her screen."""
        anchor = self._anchor_widget
        if anchor is None:
            return
        # Never move out from under a pointer that is aiming at a button.
        if self.isVisible() and self.underMouse():
            return

        anchor_rect = stacking.rect_of(anchor)
        centre = QPoint(anchor_rect[0] + anchor_rect[2] // 2, anchor_rect[1])
        screen = QApplication.screenAt(centre) or QApplication.primaryScreen()
        screen_rect = None
        if screen is not None:
            geom = screen.availableGeometry()
            screen_rect = (geom.x(), geom.y(), geom.width(), geom.height())

        x, y = stacking.place(self.width(), self.height(), anchor_rect,
                              self._obstacles(), screen_rect)
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def _follow_tick(self) -> None:
        if not self.isVisible():
            return
        self._reposition()
        self._topmost_counter += 1
        if self._topmost_counter >= 150:  # ~5s at 33ms
            self._topmost_counter = 0
            self.raise_()
            self._ensure_topmost()

    def _ensure_topmost(self) -> None:
        """Force HWND_TOPMOST, because the Qt hint alone loses to browsers."""
        try:
            import ctypes

            hwnd = int(self.winId())
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOACTIVATE = 0x0010
            HWND_TOPMOST = -1
            ctypes.windll.user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        except Exception:
            pass

    # ── Input ─────────────────────────────────────────────────────────────

    def _button_at(self, pos) -> Optional[str]:
        for name, rect in self._button_rects.items():
            if rect.contains(pos):
                return name
        return None

    def mouseMoveEvent(self, event) -> None:
        hover = self._button_at(event.pos())
        if hover != self._hover:
            self._hover = hover
            self.update()
        clickable = hover is not None or bool((self.current or {}).get("url"))
        self.setCursor(QCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor))

    def leaveEvent(self, event) -> None:
        if self._hover is not None:
            self._hover = None
            self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        button = self._button_at(event.pos())
        if button == "read":
            self.dismiss_current()
            return
        if button == "mute":
            self.mute_current()
            return

        payload = self.current
        url = (payload or {}).get("url")
        if payload and url:
            try:
                import webbrowser

                # Safe to call directly: mouse events run on the Qt main thread.
                opened = webbrowser.open(url)
            except Exception as exc:
                logger.warning("Could not open %s: %s", url, exc)
                opened = False
            self.opened.emit(payload)
            # Reading it in the browser *is* reading it, so clicking through clears it as
            # surely as "Mark as read" does -- otherwise every followed link leaves a panel
            # to tidy up afterwards. Not when the browser never opened, though: the box is
            # then the only place that link still exists.
            if opened:
                self.dismiss_current()

    # ── Painting ──────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        payload = self.current
        if payload is None:
            return

        width, height, title_y, body_y, button_y, lines = self._layout()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        panel = panel_path(0, 0, width, height)
        paint_panel(painter, panel)

        # The trim, clipped to the rounded panel so it does not square off the corners.
        colour = payload.get("colour")
        if colour:
            painter.save()
            painter.setClipPath(panel)
            painter.setPen(Qt.NoPen)
            painter.fillRect(0, 0, width, _TRIM_HEIGHT, QColor(colour))
            painter.restore()

        title_fm = QFontMetrics(self._title_font)
        painter.setFont(self._title_font)
        painter.setPen(TEXT_BRIGHT)
        painter.drawText(
            QRectF(_PADDING, title_y, width - 2 * _PADDING, title_fm.height()),
            Qt.AlignLeft | Qt.AlignVCenter,
            title_fm.elidedText(str(payload.get("title") or ""), Qt.ElideRight,
                                width - 2 * _PADDING),
        )

        body_fm = QFontMetrics(self._body_font)
        painter.setFont(self._body_font)
        painter.setPen(TEXT_BODY)
        for index, line in enumerate(lines):
            painter.drawText(
                QRectF(_PADDING, body_y + index * body_fm.lineSpacing(),
                       width - 2 * _PADDING, body_fm.lineSpacing()),
                Qt.AlignLeft | Qt.AlignVCenter, line,
            )

        small_fm = QFontMetrics(self._small_font)
        painter.setFont(self._small_font)
        self._button_rects = {}
        x = _PADDING
        for name, label in self._buttons():
            button_w = small_fm.horizontalAdvance(label) + 2 * _BUTTON_PAD
            rect = QRect(int(x), int(button_y), int(button_w), _BUTTON_HEIGHT)
            self._button_rects[name] = rect

            painter.setPen(Qt.NoPen)
            painter.setBrush(BUTTON_BG_HOVER if self._hover == name else BUTTON_BG)
            painter.drawPath(panel_path(rect.x(), rect.y(), rect.width(), rect.height(),
                                        BUTTON_RADIUS))
            if name == "mute":
                # Outlined rather than filled-and-labelled like the primary action: it is
                # the destructive one, and it should not be the button the eye lands on.
                painter.setPen(QPen(PANEL_BORDER, 1))
                painter.setBrush(Qt.NoBrush)
                painter.drawPath(panel_path(rect.x() + 0.5, rect.y() + 0.5,
                                            rect.width() - 1, rect.height() - 1,
                                            BUTTON_RADIUS))
            painter.setPen(BUTTON_TEXT)
            painter.drawText(QRectF(rect), Qt.AlignCenter, label)
            x += button_w + _BUTTON_GAP

        # How many are waiting behind this one, and whether clicking goes anywhere.
        notes_left = x
        notes_width = max(0.0, width - _PADDING - notes_left)
        notes = self._notes_text(notes_width)
        if notes:
            painter.setPen(TEXT_DIM)
            painter.drawText(
                QRectF(notes_left, button_y, notes_width, _BUTTON_HEIGHT),
                Qt.AlignRight | Qt.AlignVCenter, notes,
            )

        painter.end()
