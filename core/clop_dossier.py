"""What she has learned about other nations, kept between sessions.

4CLOP is played against people, so the useful question is rarely "what does a Coffee Farm
cost" -- it is "what has Rustlung got, and can I take them". ``viewnation.php`` answers that
in full: every building and its count, the entire garrison down to each force's weapon,
armour, size and training, plus GDP and the game's own per-tick economy table.

This is the file that remembers those answers. Nations arrive two ways: because she was asked
about one, and because one turned up in a market alert bidding against the user. The second is
the point -- the dossier fills with the people actually trading against them, without anybody
having to go looking.

Everything is stamped with when it was read, because a garrison from three days ago is a
guess, not intelligence. ``is_stale`` is what decides whether to spend a page fetch.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "clop_dossier.json"

#: Re-read a nation after this long. Buildings change slowly; garrisons change on war ticks,
#: which are twelve hours apart, so half a day is about the useful life of a reading.
DEFAULT_MAX_AGE_HOURS = 6.0

#: Past this many nations the oldest readings are dropped. A dossier is for the handful of
#: people who actually matter to the user, not a census.
MAX_NATIONS = 60


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


class DossierStore:
    """Nation and alliance readings, persisted and stamped with when they were taken."""

    def __init__(self, path: Optional[Path] = None,
                 max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self.max_age_hours = max_age_hours
        self._lock = threading.Lock()
        self._nations: Dict[str, Dict[str, Any]] = {}
        self._alliances: Dict[str, Dict[str, Any]] = {}
        #: Nations noticed but never read -- seen in a market alert, not yet looked up.
        self._seen: Dict[str, Dict[str, Any]] = {}
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
        if not isinstance(raw, dict):
            return
        for key, target in (("nations", "_nations"), ("alliances", "_alliances"),
                            ("seen", "_seen")):
            value = raw.get(key)
            if isinstance(value, dict):
                setattr(self, target, {k: v for k, v in value.items() if isinstance(v, dict)})
        logger.info("Dossier: %d nation(s), %d alliance(s), %d noticed",
                    len(self._nations), len(self._alliances), len(self._seen))

    def save(self) -> None:
        payload = {"nations": self._nations, "alliances": self._alliances, "seen": self._seen}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not write %s: %s", self.path, exc)

    # ── Noticing ──────────────────────────────────────────────────────────

    def notice(self, nation_id: int, name: str = "", why: str = "") -> bool:
        """Record that a nation turned up somewhere. Returns True if it is worth reading.

        Called when a market alert names a buyer. Cheap and side-effect free -- it does not
        fetch anything; it just marks the nation as interesting so the next sweep picks it up.
        """
        key = str(int(nation_id))
        with self._lock:
            known = self._nations.get(key)
            if known and _age_hours(known.get("read_at", "")) < self.max_age_hours:
                return False
            entry = self._seen.setdefault(key, {"nation_id": int(nation_id), "sightings": 0})
            entry["sightings"] = int(entry.get("sightings", 0)) + 1
            entry["last_seen_at"] = _now()
            if name:
                entry["name"] = name
            if why:
                entry["why"] = why
            self.save()
            return True

    def pending(self) -> List[int]:
        """Nations noticed but not read recently, most-sighted first."""
        with self._lock:
            rows = [
                entry for key, entry in self._seen.items()
                if key not in self._nations
                or _age_hours(self._nations[key].get("read_at", "")) >= self.max_age_hours
            ]
        rows.sort(key=lambda e: -int(e.get("sightings", 0)))
        return [int(e["nation_id"]) for e in rows]

    # ── Recording ─────────────────────────────────────────────────────────

    def record_nation(self, nation) -> None:
        """Store a parsed ``clop_pages.Nation``."""
        if nation.nation_id is None:
            return
        key = str(int(nation.nation_id))
        entry = {
            "nation_id": int(nation.nation_id),
            "name": nation.name,
            "region": nation.region,
            "government": nation.government,
            "economy": nation.economy,
            "leader": nation.leader,
            "alliance_id": nation.alliance_id,
            "alliance_name": nation.alliance_name,
            "age": nation.age,
            "gdp": nation.gdp,
            "buildings": dict(nation.buildings),
            "economy_rows": {k: list(v) for k, v in nation.economy_rows.items()},
            "forces": [
                {
                    "name": f.name, "type": f.type, "size": f.size,
                    "training": f.training, "weapon": f.weapon, "armor": f.armor,
                    "hostile": f.hostile,
                }
                for f in nation.forces
            ],
            "read_at": _now(),
        }
        with self._lock:
            self._nations[key] = entry
            self._seen.pop(key, None)
            if len(self._nations) > MAX_NATIONS:
                oldest = sorted(self._nations.items(),
                                key=lambda kv: kv[1].get("read_at", ""))
                for old_key, _ in oldest[: len(self._nations) - MAX_NATIONS]:
                    self._nations.pop(old_key, None)
            self.save()

    def record_alliance(self, alliance) -> None:
        if alliance.alliance_id is None:
            return
        with self._lock:
            self._alliances[str(int(alliance.alliance_id))] = {
                "alliance_id": int(alliance.alliance_id),
                "name": alliance.name,
                "members": list(alliance.members),
                "in_stasis": list(alliance.in_stasis),
                "nations": [list(n) for n in alliance.nations],
                "economy_rows": {k: list(v) for k, v in alliance.economy_rows.items()},
                "read_at": _now(),
            }
            self.save()

    # ── Reading ───────────────────────────────────────────────────────────

    def nation(self, nation_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._nations.get(str(int(nation_id)))

    def alliance(self, alliance_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._alliances.get(str(int(alliance_id)))

    def is_stale(self, nation_id: int) -> bool:
        entry = self.nation(nation_id)
        return entry is None or _age_hours(entry.get("read_at", "")) >= self.max_age_hours

    def find_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """A nation by name, so she can be asked about "Rustlung" rather than about 47."""
        wanted = " ".join(str(name).split()).casefold()
        with self._lock:
            rows = list(self._nations.values())
        for entry in rows:
            if str(entry.get("name", "")).casefold() == wanted:
                return entry
        matches = [e for e in rows if wanted in str(e.get("name", "")).casefold()]
        return matches[0] if len(matches) == 1 else None

    @property
    def nations(self) -> List[Dict[str, Any]]:
        with self._lock:
            return sorted(self._nations.values(), key=lambda e: e.get("name", ""))

    @property
    def alliances(self) -> List[Dict[str, Any]]:
        with self._lock:
            return sorted(self._alliances.values(), key=lambda e: e.get("name", ""))

    def summary(self) -> str:
        """What she knows about, and how fresh it is."""
        rows = self.nations
        if not rows:
            with self._lock:
                noticed = len(self._seen)
            return (f"No nations read yet ({noticed} noticed but not looked at)."
                    if noticed else "No nations read yet.")
        lines = [f"{len(rows)} nation(s) on file:"]
        for entry in rows:
            hours = _age_hours(entry.get("read_at", ""))
            when = "just now" if hours < 1 else f"{hours:.0f}h ago"
            forces = sum(f["size"] for f in entry.get("forces", []) if not f.get("hostile"))
            lines.append(
                f"  {entry['name']} (#{entry['nation_id']}, {entry.get('alliance_name') or 'no alliance'})"
                f" — {len(entry.get('buildings', {}))} building types, {forces} defending, read {when}"
            )
        return "\n".join(lines)


_STORES: Dict[str, "DossierStore"] = {}


def store(path: Optional[Path] = None,
          max_age_hours: Optional[float] = None) -> "DossierStore":
    """The dossier for a path, made once and reused.

    Memoised because two ``DossierStore`` objects over one file would each hold their own
    copy and overwrite the other's writes. The bridge and the lookup layer both want the
    store, and this is how they get the same one -- the same pattern ``clop_tools.gamedata``
    and ``warcalc.game_data`` already use for the static data.

    ``max_age_hours`` is only applied when passed. The bridge sets it from config; the
    lookup layer asks for the store without an opinion, and must not reset the configured
    value back to the default by doing so.
    """
    key = str(Path(path) if path else DEFAULT_PATH)
    existing = _STORES.get(key)
    if existing is None:
        existing = DossierStore(
            Path(key),
            max_age_hours=DEFAULT_MAX_AGE_HOURS if max_age_hours is None else max_age_hours,
        )
        _STORES[key] = existing
    elif max_age_hours is not None:
        existing.max_age_hours = max_age_hours
    return existing
