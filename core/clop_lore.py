"""The lorebook: game facts that appear in her context before she has to ask for them.

Mention a coffee farm and its real numbers are already in front of her when she answers.
No extra API call, no tag round trip, no cooperation needed from the model -- which means
it cannot fail the way a tool call can. This is the common case handled for free;
``[LOOKUP:...]`` in ``clop_tools`` covers what a keyword cannot catch.

The whole index is built once from ``data/gamedata.json``: 140 named things across
buildings, goods, actions, weapons, armour, unit types, governments, economies and nations.

**The matching problem.** A lot of this game's equipment is named with ordinary English
words -- ``Nope``, ``Ohno`` and ``Wonder`` are armour, ``Bar`` is a building, ``Dragon`` and
``Titan`` and ``Shining`` are armour, ``Independence`` and ``Democracy`` are governments. A
plain case-insensitive scan would drag armour stats into a conversation about dragons. So
names in ``AMBIGUOUS`` match **case-sensitively** -- "Dragon armour" hits, "that's a dragon"
does not -- while everything else, including every multi-word name, matches case-insensitively.
Anything the guard skips is still reachable with an explicit ``[LOOKUP:]``.

**The budget.** Entries run 130-180 tokens each. A message naming six things would otherwise
add a thousand tokens to a prompt that is assembled on every single call, so this caps at
``MAX_ENTRIES`` and stops. Longest match first, on the reasoning that "Machinery Parts" is a
more specific thing to have mentioned than "Parts".
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_GAMEDATA = Path(__file__).resolve().parent.parent / "data" / "gamedata.json"

#: At most this many entries injected per turn, and roughly this many characters.
MAX_ENTRIES = 3
MAX_CHARS = 2400

#: Single-word names that are also ordinary English, matched case-sensitively so normal
#: conversation does not drag game data in. Curated rather than derived: a word list would
#: be a dependency and a guess, and this set is small enough to read and argue with.
#: Kept deliberately tight. A false positive costs ~150 wasted tokens; a false negative
#: costs her the numbers she needed, and the whole point is that it just knows. So this is
#: only words that turn up constantly in conversation that has nothing to do with the game
#: -- not every word that happens to be English. "Copper" and "Tungsten" are game nouns
#: here whatever their case; "wonder" and "nope" are not.
AMBIGUOUS: frozenset = frozenset({
    # Armour named like ordinary adjectives and nouns
    "Nope", "Ohno", "Wonder", "Cooler", "Dragon", "Titan", "Shining", "Griffin",
    "Shepherd", "Librarian", "Trundle",
    # Buildings and goods that are everyday words
    "Bar", "Statue", "Energy", "Oil",
    # Governments that are ordinary political nouns
    "Democracy", "Independence",
})


@dataclass(frozen=True)
class Entry:
    """One thing she can be reminded about."""

    name: str
    kind: str                      # building | good | weapon | armor | unit | government | nation
    render: Callable[[], str]      # built lazily; formatting 140 entries at import is waste


# ── Formatters ────────────────────────────────────────────────────────────────
# Deliberately terser than the [LOOKUP:] versions. This text lands in the prompt
# unasked-for, so it earns its place by being short; a lookup she asked for can afford
# to be thorough.


def _amounts(mapping: Dict[str, Any]) -> str:
    return ", ".join(f"{v} {k}" for k, v in mapping.items()) if mapping else "nothing"


def _building(row: Dict[str, Any]) -> str:
    parts = [f"{row['name']} (building)"]
    for recipe in row.get("built_by", [])[:1]:
        cost = f"{recipe['money_cost']:,} bits"
        if recipe.get("build_consumes"):
            cost += f" + {_amounts(recipe['build_consumes'])}"
        parts.append(f"  build: {cost}")
        if recipe.get("build_requires_owned"):
            parts.append(f"  needs you to own: {_amounts(recipe['build_requires_owned'])}"
                         f" (not consumed)")
        if recipe.get("region") and recipe["region"] != "Any":
            parts.append(f"  {recipe['region']} only")
    parts.append(f"  per tick: makes {_amounts(row['produces_per_tick'])}"
                 f", eats {_amounts(row['consumes_per_tick'])}")
    extras = []
    if row.get("satisfaction_per_tick"):
        extras.append(f"{row['satisfaction_per_tick']:+d} sat")
    if row.get("gdp_per_tick"):
        extras.append(f"{row['gdp_per_tick']:+,} GDP")
    if extras:
        parts.append("  " + ", ".join(extras) + " per tick")
    pollution = row.get("pollution", {})
    if pollution.get("pollutes"):
        parts.append(f"  pollutes past {pollution['bad_min']}: "
                     f"ceil((n-{pollution['bad_min']})^2/{pollution['bad_div']}) sat")
    if row.get("description_warning"):
        parts.append(f"  NOTE: {row['description_warning']}")
    return "\n".join(parts)


def _good(row: Dict[str, Any], data: Dict[str, Any]) -> str:
    made_by = row.get("produced_by") or []
    crafted = [a["action_display_name"] for a in data["actions"]
               if a.get("produces") == row["name"]]
    parts = [f"{row['name']} (good)"]
    parts.append(f"  made by: {', '.join(made_by + crafted) or 'nothing'}")
    eaten = row.get("consumed_by") or []
    parts.append(f"  eaten per tick by: {', '.join(eaten[:8]) or 'nothing'}"
                 + (f" and {len(eaten) - 8} more" if len(eaten) > 8 else ""))
    return "\n".join(parts)


def _gear(row: Dict[str, Any], kind: str) -> str:
    stats = ", ".join(f"{k} {v}" for k, v in row["stats"].items())
    direction = "damage dealt to" if kind == "weapon" else "damage taken from (lower is better)"
    parts = [f"{row['name']} ({kind}, for {row['for_force_type']})",
             f"  {direction}: {stats}"]
    build = row.get("build") or {}
    if build:
        cost = f"{build.get('money_cost', 0):,} bits"
        if build.get("consumes"):
            cost += f" + {_amounts(build['consumes'])}"
        parts.append(f"  build: {cost}")
    return "\n".join(parts)


def _unit(name: str, row: Dict[str, Any]) -> str:
    return "\n".join([
        f"{name} (unit type)",
        f"  {row['hire_cost_per_size']:,} bits per point of size",
        f"  upkeep {_amounts(row.get('upkeep_per_size') or {})} per size, "
        f"charged on war ticks only; unpaid forces are deleted outright",
    ])


def _government(name: str, row: Dict[str, Any]) -> str:
    mult = row.get("gdp_multiplier")
    parts = [f"{name} (government)",
             f"  GDP x{mult}" if mult else "  GDP x(1 + satisfaction/1000)",
             f"  satisfaction cap {row['satisfaction_cap']}"]
    if row.get("upkeep_per_tick"):
        parts.append(f"  upkeep {_amounts(row['upkeep_per_tick'])} per tick")
    return "\n".join(parts)


def _nation(name: str, row: Dict[str, Any]) -> str:
    exclusive = ", ".join(row.get("produces_exclusively") or []) or "nothing exclusive"
    parts = [f"{name} (nation, region {row['region_id']})", f"  produces {exclusive}"]
    if row.get("note"):
        parts.append(f"  {row['note']}")
    return "\n".join(parts)


# ── Index ─────────────────────────────────────────────────────────────────────

_INDEX: Dict[str, List[Entry]] = {}


def _build_index(path: Optional[Path] = None) -> List[Entry]:
    path = Path(path or DEFAULT_GAMEDATA)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("No lorebook: could not read %s (%s)", path, exc)
        return []

    entries: List[Entry] = []
    for row in data.get("buildings", []):
        entries.append(Entry(row["name"], "building", lambda r=row: _building(r)))
    for row in data.get("goods", []):
        entries.append(Entry(row["name"], "good", lambda r=row, d=data: _good(r, d)))
    for row in data.get("weapons", []):
        entries.append(Entry(row["name"], "weapon", lambda r=row: _gear(r, "weapon")))
    for row in data.get("armor", []):
        entries.append(Entry(row["name"], "armor", lambda r=row: _gear(r, "armour")))
    for name, row in (data.get("units") or {}).items():
        entries.append(Entry(name, "unit", lambda n=name, r=row: _unit(n, r)))
    for name, row in (data.get("governments") or {}).items():
        entries.append(Entry(name, "government", lambda n=name, r=row: _government(n, r)))
    for name, row in (data.get("nations") or {}).items():
        entries.append(Entry(name, "nation", lambda n=name, r=row: _nation(n, r)))
    return entries


def entries(path: Optional[Path] = None) -> List[Entry]:
    key = str(Path(path or DEFAULT_GAMEDATA))
    if key not in _INDEX:
        _INDEX[key] = _build_index(path)
    return _INDEX[key]


_PATTERNS: Dict[str, List[Tuple[Any, Entry]]] = {}


def _patterns(path: Optional[Path] = None) -> List[Tuple[Any, Entry]]:
    """One compiled pattern per entry, longest name first.

    Longest first so "Machinery Parts" is tested before a bare "Parts" would be, and so
    the more specific mention wins when both could match at the same position.
    """
    key = str(Path(path or DEFAULT_GAMEDATA))
    if key in _PATTERNS:
        return _PATTERNS[key]

    compiled = []
    for entry in sorted(entries(path), key=lambda e: len(e.name), reverse=True):
        flags = 0 if entry.name in AMBIGUOUS else re.IGNORECASE
        compiled.append((re.compile(rf"\b{re.escape(entry.name)}\b", flags), entry))
    _PATTERNS[key] = compiled
    return compiled


def find_mentions(text: str, limit: int = MAX_ENTRIES,
                  path: Optional[Path] = None) -> List[Entry]:
    """The game things this text mentions, most specific first, capped at *limit*.

    A shorter name found entirely inside a longer one that already matched is skipped:
    "coffee farm" is a Coffee Farm, not a Coffee Farm and separately some Coffee. Sorting
    by length alone would let the shorter one through and spend a budget slot restating
    what the longer entry already covers.
    """
    if not text:
        return []
    found: List[Entry] = []
    claimed: List[Tuple[int, int]] = []

    for pattern, entry in _patterns(path):
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start >= c_start and end <= c_end for c_start, c_end in claimed):
                continue  # inside something more specific that already matched
            found.append(entry)
            claimed.append((start, end))
            break
        if len(found) >= limit:
            break
    return found


def context_for(text: str, limit: int = MAX_ENTRIES,
                path: Optional[Path] = None) -> str:
    """The reference block to put in front of a message, or "" if nothing matched.

    Returning "" rather than an empty header matters: the caller uses truthiness to decide
    whether to touch the message at all, so a turn that mentions nothing costs nothing.
    """
    found = find_mentions(text, limit, path)
    if not found:
        return ""

    blocks = []
    total = 0
    for entry in found:
        try:
            rendered = entry.render()
        except Exception as exc:  # pragma: no cover - a malformed row should not kill a turn
            logger.warning("Could not render lorebook entry %s: %s", entry.name, exc)
            continue
        if total + len(rendered) > MAX_CHARS and blocks:
            break
        blocks.append(rendered)
        total += len(rendered)

    if not blocks:
        return ""
    return (
        "== 4CLOP REFERENCE (already looked up for you) ==\n"
        "Exact numbers from the game's own data, for what was just mentioned. Use these "
        "rather than looking the same thing up again.\n\n"
        + "\n\n".join(blocks)
        + "\n\n"
    )
