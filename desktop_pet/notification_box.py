"""The box above the pony where relayed notifications land.

Modelled on ``heard_text.py`` -- same translucent dark panel, same anchor-following,
same hand-painted rendering with no child widgets -- with four differences that matter:

* **It is clickable.** ``Qt.WindowTransparentForInput`` is deliberately absent. That flag is
  what makes the other overlays pass clicks through to whatever is underneath, and it is the
  one thing a notification with a button cannot have.
* **It sits above the pony**, not below, because she is pinned to the bottom of the screen.
* **It has a 2px coloured trim** along the top when the alert is about a particular good, and
  no trim at all when it is not. See ``good_colours`` in the monitor for which is which.
* **It never hides itself.** A notification carrying a "mark as read" button should wait to be
  read. The auto-hide timer the other overlays use is gone.

Notifications arrive in bursts -- the monitor raises up to six in one poll -- so this shows one
at a time with a count of what is behind it, rather than stacking six always-on-top windows in
the corner of the screen.

Payloads are plain dicts, the shape ``clop_monitor.alert_parts`` returns, so the GUI layer never
imports the monitor's types.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import QPoint, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt5.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)

_MAX_WIDTH = 340
_PADDING = 10
_RADIUS = 8
_TRIM_HEIGHT = 2          # the coloured strip along the top
_MAX_BODY_LINES = 4       # "not too large" -- a long report is clipped, not scrolled
_BUTTON_HEIGHT = 20
_BUTTON_PAD = 8
_GAP_ABOVE_PONY = 8

_PANEL = QColor(40, 40, 40, 225)
_PANEL_BORDER = QColor(100, 100, 100, 180)
_TITLE = QColor(255, 255, 255)
_BODY = QColor(215, 215, 215)
_DIM = QColor(150, 150, 150)
_BUTTON_BG = QColor(70, 70, 70, 230)
_BUTTON_BG_HOVER = QColor(95, 95, 95, 240)
_BUTTON_TEXT = QColor(230, 230, 230)

_MARK_READ_LABEL = "Mark as read"


class NotificationBox(QWidget):
    """One notification at a time, above the pony, with the rest queued behind it."""

    #: Emitted with the payload when the reader marks one read.
    dismissed = pyqtSignal(object)
    #: Emitted with the payload when the reader clicks through to its link.
    opened = pyqtSignal(object)
    #: Emitted when the last queued notification has been cleared.
    emptied = pyqtSignal()

    def __init__(self) -> None:
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
        self._button_rect = QRect()
        self._button_hover = False

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

        # The Qt always-on-top hint alone loses to browsers, same as PetWindow finds.
        self._topmost_counter = 0

    # ── Wiring ────────────────────────────────────────────────────────────

    def set_anchor_widget(self, widget: QWidget) -> None:
        self._anchor_widget = widget

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
        self._queue.insert(0, payload)
        self._show_current()

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

    def _show_current(self) -> None:
        if not self._queue:
            self.clear()
            return
        self._button_hover = False
        self._resize_and_position()
        self.show()
        self.raise_()
        self._ensure_topmost()
        if self._anchor_widget is not None:
            self._follow_timer.start()
        self.update()

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

    def _layout(self):
        """Panel width plus the y offsets everything is drawn at."""
        payload = self.current or {}
        inner = _MAX_WIDTH - 2 * _PADDING

        title_fm = QFontMetrics(self._title_font)
        body_fm = QFontMetrics(self._body_font)

        title = str(payload.get("title") or "")
        lines = self._body_lines(str(payload.get("body") or ""), inner)

        widest = title_fm.horizontalAdvance(title)
        for line in lines:
            widest = max(widest, body_fm.horizontalAdvance(line))
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

    def _reposition(self) -> None:
        """Sit just above the pony, clamped to her screen."""
        if self._anchor_widget is None:
            return
        anchor = self._anchor_widget
        anchor_x = anchor.x() + anchor.width() // 2
        anchor_y = anchor.y()

        bx = anchor_x - self.width() // 2
        by = anchor_y - self.height() - _GAP_ABOVE_PONY

        screen = QApplication.screenAt(QPoint(anchor_x, anchor_y)) or QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            bx = max(geom.left(), min(bx, geom.right() - self.width()))
            # If there is no room above her, fall below rather than off the top.
            if by < geom.top():
                by = min(anchor.y() + anchor.height() + _GAP_ABOVE_PONY,
                         geom.bottom() - self.height())
        self.move(bx, by)

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

    def mouseMoveEvent(self, event) -> None:
        hover = self._button_rect.contains(event.pos())
        if hover != self._button_hover:
            self._button_hover = hover
            self.update()
        clickable = hover or bool((self.current or {}).get("url"))
        self.setCursor(QCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor))

    def leaveEvent(self, event) -> None:
        if self._button_hover:
            self._button_hover = False
            self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        if self._button_rect.contains(event.pos()):
            self.dismiss_current()
            return

        payload = self.current
        url = (payload or {}).get("url")
        if payload and url:
            try:
                import webbrowser

                # Safe to call directly: mouse events run on the Qt main thread.
                webbrowser.open(url)
            except Exception as exc:
                logger.warning("Could not open %s: %s", url, exc)
            self.opened.emit(payload)

    # ── Painting ──────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        payload = self.current
        if payload is None:
            return

        width, height, title_y, body_y, button_y, lines = self._layout()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        panel = QPainterPath()
        panel.addRoundedRect(QRectF(0, 0, width, height), _RADIUS, _RADIUS)
        painter.setPen(QPen(_PANEL_BORDER, 1))
        painter.setBrush(_PANEL)
        painter.drawPath(panel)

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
        painter.setPen(_TITLE)
        painter.drawText(
            QRectF(_PADDING, title_y, width - 2 * _PADDING, title_fm.height()),
            Qt.AlignLeft | Qt.AlignVCenter,
            title_fm.elidedText(str(payload.get("title") or ""), Qt.ElideRight,
                                width - 2 * _PADDING),
        )

        body_fm = QFontMetrics(self._body_font)
        painter.setFont(self._body_font)
        painter.setPen(_BODY)
        for index, line in enumerate(lines):
            painter.drawText(
                QRectF(_PADDING, body_y + index * body_fm.lineSpacing(),
                       width - 2 * _PADDING, body_fm.lineSpacing()),
                Qt.AlignLeft | Qt.AlignVCenter, line,
            )

        small_fm = QFontMetrics(self._small_font)
        button_w = small_fm.horizontalAdvance(_MARK_READ_LABEL) + 2 * _BUTTON_PAD
        self._button_rect = QRect(_PADDING, int(button_y), int(button_w), _BUTTON_HEIGHT)

        button_path = QPainterPath()
        button_path.addRoundedRect(QRectF(self._button_rect), 4, 4)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_BUTTON_BG_HOVER if self._button_hover else _BUTTON_BG)
        painter.drawPath(button_path)
        painter.setFont(self._small_font)
        painter.setPen(_BUTTON_TEXT)
        painter.drawText(QRectF(self._button_rect), Qt.AlignCenter, _MARK_READ_LABEL)

        # How many are waiting behind this one, and whether clicking goes anywhere.
        notes = []
        if len(self._queue) > 1:
            notes.append(f"{len(self._queue) - 1} more")
        if payload.get("url"):
            notes.append("click to open")
        if notes:
            painter.setPen(_DIM)
            painter.drawText(
                QRectF(_PADDING, button_y, width - 2 * _PADDING, _BUTTON_HEIGHT),
                Qt.AlignRight | Qt.AlignVCenter, " · ".join(notes),
            )

        painter.end()
