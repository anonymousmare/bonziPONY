"""Where a floating panel goes when the space above the pony is already taken.

Plain arithmetic on ``(x, y, width, height)`` tuples, with no Qt in it, because the question
"did that land on top of her speech bubble" is answerable at a desk and not by looking at a
screenshot. ``NotificationBox`` converts its ``QRect``s and hands them over.

Rectangles are exclusive at the far edge -- ``y + height`` is the first row *below* the
rectangle -- which is not what ``QRect.bottom()`` means. Convert with ``rect_of``.

The order of preference is: above everything in the way, then below everything in the way,
then beside it. Above first because that is where the box has always lived and where the eye
looks for it; beside last because it is the one that moves it out from over the pony entirely,
and is only right when there is genuinely nowhere left vertically.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

Rect = Tuple[int, int, int, int]

#: Pixels between the panel and whatever it is dodging. Matches ``panel_style.GAP``.
GAP = 8


def rect_of(widget) -> Rect:
    """One widget's frame as a rectangle. Works on anything with x/y/width/height."""
    return (widget.x(), widget.y(), widget.width(), widget.height())


def intersects(a: Rect, b: Rect) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def stack_above(rects: Sequence[Rect], x: int, w: int, h: int,
                start_top: int, limit: int) -> Optional[int]:
    """The lowest free top edge at or above ``start_top``, hopping over what is in the way.

    ``None`` when clearing everything would push the panel above ``limit`` -- the top of the
    screen. Bounded by the number of obstacles: each pass clears at least the topmost one it
    hit, and one spare pass proves the final position is free.
    """
    y = start_top
    for _ in range(len(rects) + 1):
        column = (x, y, w, h)
        hit = [r for r in rects if intersects(column, r)]
        if not hit:
            return y if y >= limit else None
        y = min(r[1] for r in hit) - GAP - h
        if y < limit:
            return None
    return None


def stack_below(rects: Sequence[Rect], x: int, w: int, h: int,
                start_top: int, limit: int) -> Optional[int]:
    """The same downwards. ``limit`` is the bottom of the screen, exclusive."""
    y = start_top
    for _ in range(len(rects) + 1):
        column = (x, y, w, h)
        hit = [r for r in rects if intersects(column, r)]
        if not hit:
            return y if y + h <= limit else None
        y = max(r[1] + r[3] for r in hit) + GAP
        if y + h > limit:
            return None
    return None


def union(rects: Sequence[Rect]) -> Rect:
    left = min(r[0] for r in rects)
    top = min(r[1] for r in rects)
    right = max(r[0] + r[2] for r in rects)
    bottom = max(r[1] + r[3] for r in rects)
    return (left, top, right - left, bottom - top)


def place(width: int, height: int, anchor: Rect,
          obstacles: Sequence[Rect], screen: Optional[Rect] = None) -> Tuple[int, int]:
    """Where to put a panel of this size: centred on ``anchor``, clear of ``obstacles``.

    ``obstacles`` is everything it must not cover, the anchor included if the anchor is one of
    them -- the caller decides, because a panel that points *at* the pony and one that must
    keep off her want different lists.
    """
    obstacles = list(obstacles)
    centre_x = anchor[0] + anchor[2] // 2
    x = centre_x - width // 2

    if screen is not None:
        sx, sy, sw, sh = screen
        x = max(sx, min(x, sx + sw - width))
        top_limit = sy
        bottom_limit = sy + sh
    else:
        top_limit = -(1 << 20)
        bottom_limit = 1 << 20

    y = stack_above(obstacles, x, width, height, anchor[1] - GAP - height, top_limit)
    if y is not None:
        return x, y

    y = stack_below(obstacles, x, width, height,
                    anchor[1] + anchor[3] + GAP, bottom_limit)
    if y is not None:
        return x, y

    if obstacles:
        # Squeezed vertically -- go beside her rather than sit on top of her.
        blob = union(obstacles)
        beside = blob[0] + blob[2] + GAP
        if screen is not None and beside + width > screen[0] + screen[2]:
            beside = blob[0] - GAP - width
        if screen is None or screen[0] <= beside <= screen[0] + screen[2] - width:
            return beside, max(top_limit, min(blob[1], bottom_limit - height))

    # Nowhere is clear. Sit where it has always sat and overlap.
    return x, max(top_limit, min(anchor[1] - GAP - height, bottom_limit - height))


def visible_rects(widgets: Sequence) -> List[Rect]:
    """The rectangles of whichever of these widgets are on screen right now."""
    rects: List[Rect] = []
    for widget in widgets:
        if widget is None:
            continue
        try:
            if widget.isVisible():
                rects.append(rect_of(widget))
        except RuntimeError:
            # The widget was destroyed underneath us. Nothing to dodge.
            continue
    return rects
