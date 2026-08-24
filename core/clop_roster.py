"""Who exists in the game at all: every nation, its id, its owner and where it is.

The dossier remembers the handful of nations she has actually *read* -- buildings, garrison,
economy -- and it only learns about someone when they turn up bidding against the user or
when they are asked about by number. That leaves a hole big enough to feel: ask her about
"Silverspire" and, unless Silverspire happened to bid on something, the answer was "no nation
called that on file". The name was never the problem. She had no phone book.

``rankings.php`` is the phone book. Four of its modes are regional rosters, and between them
they list every living nation in the game with its ``nation_id``, its owner, its subregion,
its government and its economy. It is public -- no login, nothing marked read, nothing spent
except the fetch -- and it changes about as fast as people found nations, which is to say
slowly. So it is read a few times a day at most, kept in this file, and used for two things:
turning a name into an id so ``[LOOKUP:nation:Silverspire]`` works for anyone, and answering
"who is out there" without reading forty-six pages to find out.

The scoreboards (gdp, longevity, statues) come off the same page and the same parser, but
they are a top twenty rather than a census, so they are not stored -- they go stale in a way
a roster does not, and a stale leaderboard is worse than no leaderboard.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "clop_roster.json"

#: How long a roster stays good for. Nations are founded and die on a scale of days, so this
#: is about "has anyone new appeared since this morning", not about tracking anything live.
DEFAULT_MAX_AGE_HOURS = 12.0

#: The regional rosters, as ``rankings.php?mode=`` names them. Between them these four are
#: every living nation in the game -- which is what makes them a census and the scoreboards
#: below merely a leaderboard. Region names match ``data/gamedata.json``; the mode slugs are
#: the game's own and are not derivable from them ("Saddle Arabia" is ``saddle``).
REGION_MODES: Dict[str, str] = {
    "Saddle Arabia": "saddle",
    "Zebrica": "zebrica",
    "Burrozil": "burrozil",
    "Przewalskia": "przewalskia",
}

#: The bands each region is divided into, as ``rankings.php`` prints them in its Subregion
#: column and as ``viewnation.php`` writes them into a nation's heading ("North Zebrica").
#: That heading is why they are searchable: it is how the game phrases a place, so it is how
#: people ask about one.
SUBREGIONS: Tuple[str, ...] = ("North", "Central", "South")

#: The other modes: one number, top twenty, not a census.
BOARD_MODES: Dict[str, str] = {
    "gdp": "GDP made from factories and satisfaction",
    "longevity": "the oldest nations still standing",
    "statues": "who has built statues",
}


def modes() -> Dict[str, str]:
    """Every mode ``rankings.php`` answers to -> what it lists.

    The game does not reject an unknown mode: it falls through to the regional branch and
    renders "These are the  nations." above no rows, which reads back as an empty board.
    So a mode is checked against this before it is fetched, or a typo becomes "nobody is on
    that board" -- a confident answer to a question that was never asked.
    """
    out = {mode: f"the {region} roster" for region, mode in REGION_MODES.items()}
    out.update(BOARD_MODES)
    return out


def region_for_mode(mode: str) -> Optional[str]:
    wanted = str(mode).strip().casefold()
    for region, slug in REGION_MODES.items():
        if slug == wanted:
            return region
    return None


def mode_for_region(name: str) -> Optional[str]:
    """``"burrozil"``, ``"Saddle Arabia"``, ``"saddle arabia"`` -> the mode slug."""
    wanted = " ".join(str(name).split()).casefold()
    if not wanted:
        return None
    for region, slug in REGION_MODES.items():
        if wanted in (slug, region.casefold()):
            return slug
    # "saddle" already matched above as a slug; this catches "przewalskia " typing and
    # any unambiguous prefix, e.g. "zeb".
    hits = [slug for region, slug in REGION_MODES.items()
            if region.casefold().startswith(wanted) or slug.startswith(wanted)]
    return hits[0] if len(hits) == 1 else None


def split_place(term: str) -> Optional[Tuple[str, str]]:
    """``"Central Zebrica"`` -> ``("Central", "Zebrica")``. None when it is not a place.

    Also takes the region first (``"Zebrica Central"``) and a bare band (``"Central"`` ->
    every Central nation, in all four regions). Only ever fires when the *whole* term is a
    place: "North Star" leaves "Star", which is not a region, so it falls through and is
    searched as a name -- which is what somebody asking about a nation called North Star
    meant.
    """
    words = str(term or "").split()
    if not words:
        return None

    for index, word in enumerate(words):
        band = next((s for s in SUBREGIONS if s.casefold() == word.casefold()), None)
        if band is None:
            continue
        rest = " ".join(words[:index] + words[index + 1:]).strip()
        if not rest:
            return (band, "")
        region = region_for_mode(mode_for_region(rest) or "")
        if region:
            return (band, region)
        return None
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_hours(stamp: str) -> float:
    try:
        seen = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return float("inf")
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - seen).total_seconds() / 3600.0


def _entry(row: Any) -> Dict[str, Any]:
    """A ``clop_pages.RankedNation`` (or anything shaped like one) as a plain dict."""
    return {
        "nation_id": int(getattr(row, "nation_id", 0) or 0),
        "name": str(getattr(row, "name", "") or ""),
        "region": str(getattr(row, "region", "") or ""),
        "user": str(getattr(row, "user", "") or ""),
        "user_id": getattr(row, "user_id", None),
        "subregion": str(getattr(row, "subregion", "") or ""),
        "government": str(getattr(row, "government", "") or ""),
        "economy": str(getattr(row, "economy", "") or ""),
    }


class RosterStore:
    """Every nation in the game, by region, with when each region was last read.

    Stamped per region rather than as a whole because the four fetches are separate and one
    of them can fail. A partial refresh should leave the other three exactly as they were
    instead of throwing away a good roster over one bad page.
    """

    def __init__(self, path: Optional[Path] = None,
                 max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self.max_age_hours = max_age_hours
        self._lock = threading.Lock()
        #: region -> {"read_at": iso, "nations": [entry, ...]}
        self._regions: Dict[str, Dict[str, Any]] = {}
        self.load()

    # ── Persistence ───────────────────────────────────────────────────────

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logger.warning("Could not read %s (%s) — starting empty", self.path, exc)
            return
        regions = raw.get("regions") if isinstance(raw, dict) else None
        if isinstance(regions, dict):
            self._regions = {
                key: value for key, value in regions.items()
                if isinstance(value, dict) and isinstance(value.get("nations"), list)
            }
        logger.info("Roster: %d nation(s) across %d region(s)",
                    len(self.nations), len(self._regions))

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"regions": self._regions}, indent=2),
                                 encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not write %s: %s", self.path, exc)

    # ── Recording ─────────────────────────────────────────────────────────

    def record(self, region: str, rows: Iterable[Any]) -> int:
        """Replace one region's roster. Returns how many nations it now holds.

        Replace, not merge: a nation that has left the page has been conquered or has died,
        and keeping it would leave her briefing the user about somebody who no longer exists.
        """
        entries = [_entry(row) for row in rows]
        entries = [e for e in entries if e["nation_id"] and e["name"]]
        with self._lock:
            self._regions[str(region)] = {"read_at": _now(), "nations": entries}
            self.save()
        return len(entries)

    # ── Reading ───────────────────────────────────────────────────────────

    @property
    def regions(self) -> List[str]:
        with self._lock:
            return sorted(self._regions)

    @property
    def nations(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = [dict(entry) for value in self._regions.values()
                    for entry in value.get("nations", [])]
        return sorted(rows, key=lambda e: str(e.get("name", "")).casefold())

    def in_place(self, subregion: str = "", region: str = "") -> List[Dict[str, Any]]:
        """Nations in one band, optionally of one region: the "Central Zebrica" question."""
        band = str(subregion or "").casefold()
        rows = self.in_region(region) if region else self.nations
        if not band:
            return rows
        return [e for e in rows if str(e.get("subregion", "")).casefold() == band]

    def in_region(self, region: str) -> List[Dict[str, Any]]:
        with self._lock:
            value = self._regions.get(str(region)) or {}
            rows = [dict(entry) for entry in value.get("nations", [])]
        return sorted(rows, key=lambda e: str(e.get("name", "")).casefold())

    @property
    def empty(self) -> bool:
        return not self.nations

    def age_hours(self, region: Optional[str] = None) -> float:
        """How long ago this was read. ``inf`` for never."""
        with self._lock:
            if region is not None:
                return _age_hours((self._regions.get(str(region)) or {}).get("read_at", ""))
            stamps = [value.get("read_at", "") for value in self._regions.values()]
        if len(stamps) < len(REGION_MODES):
            return float("inf")  # a region has never been read at all
        return max(_age_hours(stamp) for stamp in stamps) if stamps else float("inf")

    def is_stale(self, region: Optional[str] = None) -> bool:
        return self.age_hours(region) >= self.max_age_hours

    def get(self, nation_id: int) -> Optional[Dict[str, Any]]:
        wanted = int(nation_id)
        for entry in self.nations:
            if entry.get("nation_id") == wanted:
                return entry
        return None

    def find(self, term: str) -> List[Dict[str, Any]]:
        """Nations matching a name -- exact first, then prefix, then substring, then owner.

        Each tier is returned alone if it hits, so an exact name is never buried under the
        nations that merely contain it: "Mareconesia" must not come back as three maybes
        because two other names have "mare" in them.
        """
        wanted = " ".join(str(term).split()).casefold()
        if not wanted:
            return []
        rows = self.nations

        def named(test) -> List[Dict[str, Any]]:
            return [e for e in rows if test(str(e.get("name", "")).casefold())]

        for test in (lambda n: n == wanted,
                     lambda n: n.startswith(wanted),
                     lambda n: wanted in n):
            hits = named(test)
            if hits:
                return hits
        # Players talk about each other by handle as often as by nation name.
        return [e for e in rows if wanted in str(e.get("user", "")).casefold()]

    def resolve(self, term: str) -> Optional[Dict[str, Any]]:
        """One nation for a name, or None when it is unknown or ambiguous."""
        hits = self.find(term)
        return hits[0] if len(hits) == 1 else None


_STORES: Dict[str, "RosterStore"] = {}


def store(path: Optional[Path] = None,
          max_age_hours: Optional[float] = None) -> "RosterStore":
    """The roster for a path, made once and reused.

    Memoised for the same reason ``clop_dossier.store`` is: two stores over one file would
    each hold their own copy and overwrite the other's writes. ``max_age_hours`` is applied
    only when passed, so the lookup layer asking for the store cannot reset the window the
    bridge configured.
    """
    key = str(Path(path) if path else DEFAULT_PATH)
    existing = _STORES.get(key)
    if existing is None:
        existing = RosterStore(
            Path(key),
            max_age_hours=DEFAULT_MAX_AGE_HOURS if max_age_hours is None else max_age_hours,
        )
        _STORES[key] = existing
    elif max_age_hours is not None:
        existing.max_age_hours = max_age_hours
    return existing
