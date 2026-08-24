"""Small overlay showing what the STT heard, under the pony where there is room for it.

Same panel as the notification box and the speech bubble, from ``panel_style``, with a pointer
at whichever edge faces her. She is pinned to a screen corner by default, and in a bottom
corner there is no room underneath, so the panel goes above her instead -- and the pointer has
to follow. One that always pointed up left the panel sitting above her aiming at the ceiling.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, QTimer, QRectF
from PyQt5.QtGui import QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QApplication, QWidget

from desktop_pet.panel_style import (
    BORDER_WIDTH,
    PANEL_BG,
    PANEL_BORDER,
    RADIUS as _RADIUS,
    TEXT_BODY,
)

_MAX_WIDTH = 350
_PADDING = 8
_POINTER_SIZE = 8


class HeardText(QWidget):
    """Translucent overlay showing what the STT transcribed."""

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self._text = ""
        self._anchor_widget = None
        #: Which edge the pointer sits on. False when the panel had to go above her, which
        #: is the normal case for the default bottom-right pin.
        self._pointer_up = True

        self._font = QFont("Segoe UI", 9)
        self._font.setItalic(True)
        self._font.setStyleStrategy(QFont.PreferAntialias)

        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(33)
        self._follow_timer.timeout.connect(self._follow_tick)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide_heard)

    def set_anchor_widget(self, widget) -> None:
        self._anchor_widget = widget

    def show_heard(self, text: str) -> None:
        """Show what the STT heard below the pony."""
        self._hide_timer.stop()
        self._text = text.strip()
        if not self._text:
            self.hide_heard()
            return
        self._resize_and_position()
        self.show()
        self.raise_()
        if self._anchor_widget:
            self._follow_timer.start()
        # Auto-hide after a few seconds (will be replaced by speech bubble anyway)
        self._hide_timer.start(6000)

    def hide_heard(self) -> None:
        self._hide_timer.stop()
        self._follow_timer.stop()
        self.hide()

    def _follow_tick(self) -> None:
        if not self.isVisible() or self._anchor_widget is None:
            return
        self._reposition()

    def _resize_and_position(self) -> None:
        fm = QFontMetrics(self._font)
        text_rect = fm.boundingRect(
            0, 0, _MAX_WIDTH - 2 * _PADDING, 1000,
            Qt.TextWordWrap, self._text or " ",
        )
        w = min(max(text_rect.width() + 2 * _PADDING, 60), _MAX_WIDTH)
        h = text_rect.height() + 2 * _PADDING + _POINTER_SIZE
        self.setFixedSize(int(w), int(h))
        self._reposition()

    def _reposition(self) -> None:
        if self._anchor_widget is None:
            return
        w = self._anchor_widget
        anchor_x = w.x() + w.width() // 2
        anchor_y = w.y() + w.height()

        bx = anchor_x - self.width() // 2
        by = anchor_y + 4  # small gap below sprite
        pointer_up = True

        # Clamp to screen
        from PyQt5.QtCore import QPoint
        screen = QApplication.screenAt(QPoint(anchor_x, anchor_y))
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            bx = max(geom.left(), min(bx, geom.right() - self.width()))
            by_clamped = min(by, geom.bottom() - self.height())
            # If clamped position overlaps anchor, flip above the pony
            if by_clamped < anchor_y + 4:
                by = max(geom.top(), w.y() - self.height() - 4)
                pointer_up = False
            else:
                by = by_clamped

        if pointer_up != self._pointer_up:
            self._pointer_up = pointer_up
            self.update()   # the pointer is painted, so a flip has to repaint

        self.move(bx, by)

    def paintEvent(self, event) -> None:
        if not self._text:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        fm = QFontMetrics(self._font)
        text_rect = fm.boundingRect(
            0, 0, _MAX_WIDTH - 2 * _PADDING, 1000,
            Qt.TextWordWrap, self._text,
        )
        bubble_w = min(max(text_rect.width() + 2 * _PADDING, 60), _MAX_WIDTH)
        bubble_h = text_rect.height() + 2 * _PADDING
        # The pointer's strip of height is above the panel or below it, never both: the
        # widget is only tall enough for one.
        bubble_y = _POINTER_SIZE if self._pointer_up else 0

        # Bubble background
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, bubble_y, bubble_w, bubble_h), _RADIUS, _RADIUS)
        painter.setPen(QPen(PANEL_BORDER, BORDER_WIDTH))
        painter.setBrush(PANEL_BG)
        painter.drawPath(path)

        # Pointer triangle, on the edge that faces her
        ptr_path = QPainterPath()
        cx = bubble_w // 2
        tip_y = 1 if self._pointer_up else bubble_y + bubble_h + _POINTER_SIZE - 1
        base_y = bubble_y if self._pointer_up else bubble_y + bubble_h
        ptr_path.moveTo(cx - 5, base_y)
        ptr_path.lineTo(cx, tip_y)
        ptr_path.lineTo(cx + 5, base_y)
        ptr_path.closeSubpath()
        painter.setBrush(PANEL_BG)
        painter.setPen(QPen(PANEL_BORDER, BORDER_WIDTH))
        painter.drawPath(ptr_path)

        # Fill seam. Source rather than the default: the panel colour is translucent, so
        # painting it a second time over the border would show as a lighter band instead of
        # erasing the line.
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.setPen(Qt.NoPen)
        painter.setBrush(PANEL_BG)
        seam_y = bubble_y if self._pointer_up else bubble_y + bubble_h - 2
        painter.drawRect(int(cx) - 4, int(seam_y), 8, 3)
        painter.restore()

        # Text
        painter.setPen(TEXT_BODY)
        painter.setFont(self._font)
        painter.drawText(
            QRectF(_PADDING, bubble_y + _PADDING,
                   bubble_w - 2 * _PADDING, bubble_h - 2 * _PADDING),
            Qt.TextWordWrap, self._text,
        )

        painter.end()
