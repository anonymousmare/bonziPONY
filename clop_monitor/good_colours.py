#!/usr/bin/env python3
"""A trim colour per good, and how to find a good's name inside prose.

Windows toasts cannot be coloured, so the sprite carries the identity there (see the icon notes in
``README.md``). A notification surface that *can* paint -- the desktop pet's box -- wants a colour
instead, and this is where that lives.

These colours are a presentation choice, not game data: ``resourcedefs`` has no colour column and
the game never shows one. ``goods.py`` deliberately knows the game's vocabulary and nothing about
how anything is displayed, so the mapping sits here rather than as another field on ``Good``.

Fourteen of the thirty-one goods have no colour -- the twelve DNA variants, Forbidden Research and
Apotheosis Serum. ``colour_for`` returns ``None`` for those and a caller is expected to draw no
trim at all, not to substitute a default. "If applicable" is the whole rule.

Colours repeat on purpose. Five pairs share a value (Oil/Vehicle Parts, Copper/Coffee,
Machinery Parts/Gasoline, Gems/Plastics, Composites/Toys). A trim is a glance-level hint, not an
identifier, and the alert's own text is what actually names the good -- so nothing here needs the
mapping to be injective, and nothing may come to depend on it being so.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from goods import BY_GAME_NAME, GOODS, Good

#: ``game_name`` -> ``#RRGGBB``. Goods absent from this table have no colour; see the module
#: docstring. Keys are checked against ``goods.GOODS`` at import, so a typo or a renamed good
#: fails loudly here instead of silently painting nothing.
COLOURS: Dict[str, str] = {
    "Oil": "#FF0000",
    "Copper": "#FF6A00",
    "Apples": "#B6FF00",
    "Energy": "#00FFFF",
    "Vehicle Parts": "#FF0000",
    "Machinery Parts": "#0094FF",
    "Pies": "#FF00DC",
    "Cider": "#FFD800",
    "Coffee": "#FF6A00",
    "Gasoline": "#0094FF",
    "Gems": "#00FF21",
    "Tungsten": "#808080",
    "Plastics": "#00FF21",
    "Precision Parts": "#B200FF",
    "Composites": "#FFFFFF",
    "Drugs": "#FF006E",
    "Toys": "#FFFFFF",
}

_unknown = sorted(set(COLOURS) - set(BY_GAME_NAME))
if _unknown:  # pragma: no cover - import-time guard
    raise RuntimeError(f"good_colours.COLOURS names goods that do not exist: {_unknown}")
del _unknown


def colour_for(game_name: str) -> Optional[str]:
    """Return the trim colour for a good, or ``None`` if it has none.

    Takes the game's own name for the good -- the same string ``Alert.icon_key`` carries for a
    market alert, so that path needs no guessing at all.
    """
    return COLOURS.get(game_name)


#: Longest names first, so "Machinery Parts" is tested before "Parts" would be, and "Precision
#: Parts" before "Parts". Built once at import because the vocabulary cannot change at runtime.
_NAMES_LONGEST_FIRST: Tuple[str, ...] = tuple(
    sorted((good.game_name for good in GOODS), key=len, reverse=True)
)

#: One alternation over every good name, anchored to word boundaries so "Gem" in a nation's name
#: cannot match "Gems". Names are escaped because several contain a hyphen.
_ANY_GOOD = re.compile(
    r"\b(" + "|".join(re.escape(name) for name in _NAMES_LONGEST_FIRST) + r")\b",
    re.IGNORECASE,
)


def find_good_in_text(text: str) -> Optional[Good]:
    """Return the good a piece of prose is most plausibly about, or ``None``.

    Report bodies are free text -- the game writes them, not us -- so unlike a market alert there
    is no field naming the good. The earliest mention wins, on the reasoning that a tick report
    leads with what it is about; ties go to the longer name so "Machinery Parts" beats a bare
    "Parts" starting at the same offset.

    This is a hint for painting a 2px trim and nothing more. It will happily match the good inside
    a building's name ("Basic Oil Well" reads as Oil), which is the right answer often enough and
    harmless when it is not, because the alert's text is what the reader actually acts on.
    """
    best: Optional[Good] = None
    best_at = len(text) + 1
    best_len = 0

    for match in _ANY_GOOD.finditer(text):
        good = BY_GAME_NAME.get(match.group(1))
        if good is None:
            # Matched case-insensitively; recover the canonical row.
            lowered = match.group(1).lower()
            good = next(
                (g for g in GOODS if g.game_name.lower() == lowered),
                None,
            )
        if good is None:  # pragma: no cover - the alternation is built from GOODS
            continue
        start = match.start()
        length = len(match.group(1))
        if start < best_at or (start == best_at and length > best_len):
            best, best_at, best_len = good, start, length

    return best


def colour_for_text(text: str) -> Optional[str]:
    """Return the trim colour for whichever good the prose is about, or ``None``."""
    good = find_good_in_text(text)
    return colour_for(good.game_name) if good else None
