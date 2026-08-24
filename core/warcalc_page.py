"""Hand a battle over to the warcalc page so the user can push the numbers around.

``core/warcalc.py`` answers "can I take them" in one shot: she runs the simulation and reads
the casualties out. That is the right shape for a spoken answer and the wrong shape for the
question that always comes next -- what if I bring twenty more, what if they are the ones
attacking, what if I re-equip the pegasi. Every one of those is another round trip through
her, and none of them are questions she is better at than a form with the numbers already in
it.

``tools/warcalc.html`` is that form: the same simulation, ported to JavaScript, with sprites,
costs and the game's own styling. This module opens it with the battle she just ran already
filled in, so the follow-up questions cost the user a couple of clicks instead of a
conversation.

The battle travels in the URL fragment. That is deliberate: a fragment is never sent to a
server even when a page is served over http, and over ``file://`` there is no server to send
it to. Nothing about the user's game leaves their machine to make this work.
"""

from __future__ import annotations

import base64
import json
import logging
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_PAGE = Path(__file__).resolve().parent.parent / "tools" / "warcalc.html"

#: The page's own keys, from its WEAPON_IDS / ARMOR_IDS / LOADOUTS tables. Kept here rather
#: than parsed out of the HTML because a silent mismatch is the failure that matters: an
#: unmapped name does not error in the browser, it quietly becomes Scrounged, and she would
#: be describing a battle the page is not showing.
WEAPON_KEYS = (
    "ScroungedWeapons", "PRC-E6", "PRC-E7", "PRC-E8", "ACFU", "ATFU", "APFU", "AUFU",
    "K9P", "ELBO-GRS", "Chem-LightBattery", "PropWash", "SteamBucket", "CanopyLights",
    "LongStand", "LongWeight", "GridSquares", "Shoreline", "WaterHammer", "WaterlineEraser",
)

ARMOR_KEYS = (
    "ScroungedArmor", "Barding", "Bigdog", "Nope", "Trundle", "Shepherd", "Ohno", "Titan",
    "Cooler", "Wonder", "Griffin", "Dragon", "Hornshield", "Librarian", "Shining", "D2A",
    "C-PON3", "Esohes", "Shubidu",
)

FORCE_TYPES = ("Cavalry", "Tanks", "Pegasi", "Unicorns", "Naval", "Alicorns")

_WEAPONS = {k.casefold(): k for k in WEAPON_KEYS}
_ARMOR = {k.casefold(): k for k in ARMOR_KEYS}
_TYPES = {t.casefold(): t for t in FORCE_TYPES}


def _key(name: str) -> str:
    """``"Grid Squares"`` -> ``"GridSquares"``.

    The game prints equipment with spaces and the page names it without them; that is the
    whole of the difference, and the page's own paste-import does the same thing.
    """
    return "".join(str(name or "").split())


def _force(raw: Dict[str, Any], side: str) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """One force in the page's shape, plus anything that had to be changed to get there."""
    notes: List[str] = []
    kind = _TYPES.get(str(raw.get("type", "")).strip().casefold())
    if kind is None:
        return None, [f"dropped {raw.get('type', '?')!r}: not a force type the page knows"]

    if kind == "Alicorns" and side == "attacker":
        # The game will not let them attack, so neither will the page: its attacker dropdown
        # has no such option and the row would silently read as empty.
        return None, ["dropped the attacking alicorns: they can only defend"]

    weapon = _WEAPONS.get(_key(raw.get("weapon", "")).casefold(), "")
    armor = _ARMOR.get(_key(raw.get("armor", "")).casefold(), "")
    if raw.get("weapon") and not weapon:
        notes.append(f"{raw['weapon']!r} is not equipment the page knows — sent as scrounged")
    if raw.get("armor") and not armor:
        notes.append(f"{raw['armor']!r} is not armour the page knows — sent as scrounged")

    return {
        "type": kind,
        "weapon": weapon or "ScroungedWeapons",
        "armor": armor or "ScroungedArmor",
        "size": max(0, int(raw.get("size", 0) or 0)),
        "training": max(0, int(raw.get("training", 0) or 0)),
    }, notes


def build_payload(
    attackers: Sequence[Dict[str, Any]],
    defenders: Sequence[Dict[str, Any]],
    defender_bonus: bool = True,
    title: str = "",
) -> Tuple[Dict[str, Any], List[str]]:
    """The battle as the page wants it, and the notes about anything that did not translate."""
    notes: List[str] = []
    sides: Dict[str, Any] = {}
    for key, rows, side in (("attackers", attackers, "attacker"),
                            ("defenders", defenders, "defender")):
        out = []
        for raw in rows or ():
            force, why = _force(raw, side)
            notes.extend(why)
            if force is not None:
                out.append(force)
        sides[key] = out
    sides["bonus"] = bool(defender_bonus)
    if title:
        sides["title"] = str(title)[:120]
    return sides, notes


def fragment(payload: Dict[str, Any]) -> str:
    """``#w=<base64url>``. Base64 so no amount of quoting in a nation name can break the URL."""
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return "w=" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def page_url(payload: Dict[str, Any], page: Optional[Path] = None) -> str:
    """A ``file://`` URL for the page with this battle in its fragment."""
    path = Path(page) if page else DEFAULT_PAGE
    return path.resolve().as_uri() + "#" + fragment(payload)


def open_battle(
    attackers: Sequence[Dict[str, Any]],
    defenders: Sequence[Dict[str, Any]],
    defender_bonus: bool = True,
    title: str = "",
    page: Optional[Path] = None,
) -> Tuple[Optional[str], List[str]]:
    """Open the warcalc page on this battle. Returns the URL opened, or None with a reason.

    Never raises. A browser that will not open is a worse answer than the numbers she already
    read out, not a reason to lose them.
    """
    path = Path(page) if page else DEFAULT_PAGE
    if not path.is_file():
        return None, [f"no warcalc page at {path}"]

    payload, notes = build_payload(attackers, defenders, defender_bonus, title)
    if not payload["attackers"] and not payload["defenders"]:
        return None, notes + ["nothing left to show once the forces were checked"]

    url = page_url(payload, path)
    try:
        opened = webbrowser.open(url)
    except Exception as exc:  # pragma: no cover - depends on the desktop
        logger.warning("Could not open the warcalc page: %s", exc)
        return None, notes + [f"could not open a browser ({exc})"]
    if not opened:
        return None, notes + ["no browser would open"]
    return url, notes
