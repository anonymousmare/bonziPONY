"""Which relayed notifications are worth showing, and which the user already knows.

The monitor raises an alert for everything that changed. Some of those changes are things
the user did on purpose thirty seconds ago -- buying copper produces "Buy orders for Copper",
which is news to nobody. Switching that off used to mean editing ``market.goods`` in the
monitor's own ``settings.json``, inside a vendored checkout that is meant to stay exactly as
upstream shipped it (see ``core/clop_bridge``). So the muting lives here instead, in the pet's
own file, at two grains:

* **by kind** -- the six categories ``clop_monitor.alert_category`` returns, so "no news, ever"
  is one toggle;
* **by subject** -- the particular good a market alert is about, so copper can be silent while
  every other order still arrives.

A subject is the alert's ``icon_key``, which for a market alert is the good's ``game_name``.
``PetSink`` copies it onto the payload as ``subject`` because ``alert_parts`` does not carry it
-- the trim colour is derived from it and then it is dropped. Payloads written by an older
build have no such key, and payloads restored from ``clop_unread.json`` may be any age, so
``subject_of`` falls back to reading the good out of the title the same way the alert wrote it.

No Qt and no monitor imports: the poll thread asks ``allows`` and the GUI thread calls
``mute``/``set_category``, so everything is under one lock and the file is rewritten on
every change. It is small and changes only when a human clicks something.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "notify_filters.json"

#: The alert kinds, in the order a settings list should show them, with the label the menu
#: uses and the word a sentence uses. Keys are what ``clop_monitor.alert_category`` returns:
#: its four ``CATEGORY_ICON_KEYS`` plus ``market`` and ``other``.
CATEGORIES: Tuple[Tuple[str, str, str], ...] = (
    ("report", "Reports", "reports"),
    ("messages", "Messages", "message alerts"),
    ("news", "News", "news updates"),
    ("market", "Market orders", "market alerts"),
    ("4chan", "Thread posts", "thread posts"),
    ("other", "Everything else", "notices"),
)

CATEGORY_KEYS: Tuple[str, ...] = tuple(key for key, _, _ in CATEGORIES)

#: "Buy orders for Copper" -> Copper. Only consulted when the payload has no ``subject``,
#: which means it predates that key or came back from a stored catch-up.
_SUBJECT_IN_TITLE = re.compile(r"\bfor\s+([A-Za-z][A-Za-z0-9' .-]*?)\s*$")


def category_label(category: str) -> str:
    """The menu label for a category key, or the key itself if it is one we don't know."""
    for key, label, _ in CATEGORIES:
        if key == category:
            return label
    return category or "other"


def category_noun(category: str) -> str:
    """How a category reads inside a sentence: "muted news updates"."""
    for key, _, noun in CATEGORIES:
        if key == category:
            return noun
    return f"{category} alerts" if category else "notices"


def subject_of(payload: Dict[str, Any]) -> str:
    """The good a market alert is about, or "" for an alert that is about no good.

    Only market alerts have one. A report that happens to mention copper is still a report:
    muting the good should silence the order lists, not the war.
    """
    if not isinstance(payload, dict):
        return ""
    if str(payload.get("category") or "") != "market":
        return ""
    subject = str(payload.get("subject") or "").strip()
    if subject:
        return subject
    match = _SUBJECT_IN_TITLE.search(str(payload.get("title") or "").strip())
    return match.group(1).strip() if match else ""


class NotifyFilter:
    """Muted kinds and muted goods, persisted, asked once per alert."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self._lock = threading.Lock()
        #: Only the categories somebody has actually set. Missing means on.
        self._categories: Dict[str, bool] = {}
        #: Muted goods, lowercased key -> the spelling to show in the settings list.
        self._subjects: Dict[str, str] = {}
        self.load()

    # ── Persistence ───────────────────────────────────────────────────────

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logger.warning("Could not read %s (%s) — no notifications are muted", self.path, exc)
            return
        if not isinstance(raw, dict):
            return
        categories = raw.get("categories")
        if isinstance(categories, dict):
            self._categories = {
                str(k): bool(v) for k, v in categories.items() if isinstance(v, bool)
            }
        subjects = raw.get("subjects")
        if isinstance(subjects, list):
            self._subjects = {
                str(s).strip().lower(): str(s).strip()
                for s in subjects
                if isinstance(s, str) and s.strip()
            }
        if self.muted_categories() or self._subjects:
            logger.info("Notification filter: %d kind(s) and %d good(s) muted",
                        len(self.muted_categories()), len(self._subjects))

    def save(self) -> None:
        payload = {
            "categories": dict(self._categories),
            "subjects": sorted(self._subjects.values(), key=str.lower),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not write %s: %s", self.path, exc)

    # ── The question the poll thread asks ─────────────────────────────────

    def allows(self, payload: Dict[str, Any]) -> bool:
        """True if this alert should reach the box at all."""
        if not isinstance(payload, dict):
            return True
        category = str(payload.get("category") or "other")
        subject = subject_of(payload)
        with self._lock:
            if not self._categories.get(category, True):
                return False
            return subject.lower() not in self._subjects

    # ── Kinds ─────────────────────────────────────────────────────────────

    def category_enabled(self, category: str) -> bool:
        with self._lock:
            return self._categories.get(str(category), True)

    def set_category(self, category: str, enabled: bool) -> None:
        with self._lock:
            self._categories[str(category)] = bool(enabled)
        self.save()

    def muted_categories(self) -> List[str]:
        with self._lock:
            return sorted(key for key, on in self._categories.items() if not on)

    # ── Goods ─────────────────────────────────────────────────────────────

    def subject_muted(self, subject: str) -> bool:
        with self._lock:
            return str(subject).strip().lower() in self._subjects

    def mute_subject(self, subject: str) -> None:
        name = str(subject).strip()
        if not name:
            return
        with self._lock:
            self._subjects[name.lower()] = name
        self.save()

    def unmute_subject(self, subject: str) -> None:
        with self._lock:
            self._subjects.pop(str(subject).strip().lower(), None)
        self.save()

    def muted_subjects(self) -> List[str]:
        with self._lock:
            return sorted(self._subjects.values(), key=str.lower)

    # ── What a "mute this" button does ────────────────────────────────────

    def mute_target(self, payload: Dict[str, Any]) -> Tuple[str, str, str]:
        """What muting this alert would silence: ``(kind, value, label)``.

        ``kind`` is "subject" or "category"; ``value`` is what to pass to ``mute_subject`` or
        ``set_category``; ``label`` is the button's text. A market alert offers its good,
        because "no more copper" is almost always what is meant and "no market alerts at all"
        is a setting, not a reflex.
        """
        subject = subject_of(payload if isinstance(payload, dict) else {})
        if subject:
            return "subject", subject, f"Mute {subject}"
        category = str((payload or {}).get("category") or "other")
        return "category", category, f"Mute {category_noun(category)}"

    def mute(self, payload: Dict[str, Any]) -> str:
        """Mute whatever this alert is about. Returns what was muted, for a log line."""
        kind, value, _ = self.mute_target(payload)
        if kind == "subject":
            self.mute_subject(value)
            return value
        self.set_category(value, False)
        return category_noun(value)

    def unmute_all(self) -> None:
        with self._lock:
            self._categories.clear()
            self._subjects.clear()
        self.save()

    # ── Reporting ─────────────────────────────────────────────────────────

    def summary(self) -> str:
        """One line for the menu: what is currently switched off."""
        kinds = [category_label(key) for key in self.muted_categories()]
        goods = self.muted_subjects()
        if not kinds and not goods:
            return "showing everything"
        parts = []
        if kinds:
            parts.append(", ".join(kinds).lower())
        if goods:
            parts.append("goods " + ", ".join(goods))
        return "muted: " + "; ".join(parts)
