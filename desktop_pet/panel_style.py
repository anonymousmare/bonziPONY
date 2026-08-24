"""The one palette every floating panel above the pony is painted from.

``notification_box``, ``heard_text`` and the speech bubble are three windows that sit within a
few pixels of each other and used to be three different designs -- a dark translucent panel, a
dark translucent panel with a slightly different alpha, and a white comic bubble with a 2px
black outline. The white one read as a different program's window.

So the colours and the geometry live here and each widget draws itself from them. Anything that
wants to look like it belongs above the pony should import from this rather than writing its own
``QColor(40, 40, 40, 225)``.

The pixel-font ("m5x7") speech bubble is deliberately outside all of this: it has no panel at
all, just haloed text like a Minecraft nametag, and that is the point of it.
"""

from __future__ import annotations

from PyQt5.QtCore import QRectF
from PyQt5.QtGui import QColor, QPainterPath, QPen

#: Pixels between a panel and whatever it is pointing at or dodging. Defined in ``stacking``,
#: which is where the placement maths needs it and which has no Qt in it, and re-exported here
#: so a widget spacing itself by eye and one being placed by ``stacking.place`` agree.
from desktop_pet.stacking import GAP  # noqa: F401

#: The panel itself. Opaque enough to read over a busy desktop, translucent enough to be
#: obviously an overlay rather than a window.
PANEL_BG = QColor(40, 40, 40, 225)
PANEL_BORDER = QColor(100, 100, 100, 180)

#: Headings and anything that has to be read first.
TEXT_BRIGHT = QColor(255, 255, 255)
#: Body copy: what she said, what the alert says.
TEXT_BODY = QColor(224, 224, 224)
#: Counts, hints, "2 more" -- present but not competing.
TEXT_DIM = QColor(150, 150, 150)

#: The small buttons ("Mark as read", "Mute Copper").
BUTTON_BG = QColor(70, 70, 70, 230)
BUTTON_BG_HOVER = QColor(95, 95, 95, 240)
BUTTON_TEXT = QColor(230, 230, 230)

RADIUS = 8
#: Buttons are a third the height of a panel; the panel radius on one is a pill.
BUTTON_RADIUS = 4
BORDER_WIDTH = 1


def panel_path(x: float, y: float, width: float, height: float,
               radius: float = RADIUS) -> QPainterPath:
    """The rounded rectangle every panel is, as a path so it can also be a clip region."""
    path = QPainterPath()
    path.addRoundedRect(QRectF(x, y, width, height), radius, radius)
    return path


def paint_panel(painter, path: QPainterPath) -> None:
    """Fill and outline a panel path with the shared colours."""
    painter.setPen(QPen(PANEL_BORDER, BORDER_WIDTH))
    painter.setBrush(PANEL_BG)
    painter.drawPath(path)
