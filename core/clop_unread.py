"""What the CLOP monitor said while nobody was looking.

The monitor is level-triggered for two of its six alert kinds: "3 unread user message(s)
pending" and the market buy-order lists re-fire on *every* poll for as long as the condition
holds, not once when it starts. At a 60s poll an hour away from the desk is sixty copies of the
same sentence. So this store is keyed by the alert's text: adding one that is already unread
refreshes its timestamp and bumps a count, rather than appending.

It persists, because the catch-up is worth as much after a restart as after a coffee break --
arguably more, since a restart is when the in-memory baseline is gone.

Nothing here talks to the GUI or to the monitor. It is handed payload dicts (the shape
``clop_monitor.alert_parts`` returns) and hands back counts and summaries.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "clop_unread.json"

#: How each bucket reads out loud, singular and plural. Mostly these are the categories
#: ``clop_monitor.alert_category`` returns, except that message alerts are split by who sent
#: them -- "3 missed user messages" is the useful sentence, "1 message alert" is not.
_BUCKET_WORDS = {
    "report": ("missed report", "missed reports"),
    "user messages": ("missed user message", "missed user messages"),
    "alliance messages": ("missed alliance message", "missed alliance messages"),
    "messages": ("missed message", "missed messages"),
    "news": ("news update", "news updates"),
    "market": ("market alert", "market alerts"),
    "4chan": ("thread post", "thread posts"),
    "other": ("notice", "notices"),
}

#: The count a level-triggered message alert reports, e.g. "3 unread user message(s) pending".
_LEADING_COUNT = re.compile(r"^\s*(\d+)\b")

#: Beyond this the oldest are dropped. A catch-up naming two hundred things is not a catch-up,
#: and the counts still reflect everything that arrived.
MAX_ITEMS = 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class UnreadStore:
    """Unread notifications, deduplicated by text and persisted across restarts."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self._lock = threading.Lock()
        self._items: List[Dict[str, Any]] = []
        #: Total arrivals per category, repeats included. Diagnostic only -- ``counts`` reads
        #: the distinct items instead, for the reason given on ``_bucket``.
        self._seen_total: Dict[str, int] = {}
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
        items = raw.get("unread")
        if isinstance(items, list):
            self._items = [i for i in items if isinstance(i, dict)]
        totals = raw.get("seen_total")
        if isinstance(totals, dict):
            self._seen_total = {k: int(v) for k, v in totals.items() if isinstance(v, int)}
        logger.info("Loaded %d unread CLOP notification(s)", len(self._items))

    def save(self) -> None:
        payload = {"unread": self._items, "seen_total": self._seen_total}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not write %s: %s", self.path, exc)

    # ── Mutation ──────────────────────────────────────────────────────────

    @staticmethod
    def _key(payload: Dict[str, Any]) -> str:
        return f"{payload.get('title', '')}\n{payload.get('body', '')}"

    def add(self, payload: Dict[str, Any]) -> bool:
        """Record one arrival. Returns True if it is new rather than a repeat."""
        if not isinstance(payload, dict):
            return False
        key = self._key(payload)
        category = str(payload.get("category") or "other")
        with self._lock:
            self._seen_total[category] = self._seen_total.get(category, 0) + 1
            for item in self._items:
                if item.get("_key") == key:
                    item["last_at"] = _now()
                    item["repeats"] = int(item.get("repeats", 0)) + 1
                    self.save()
                    return False
            record = dict(payload)
            record["_key"] = key
            record["first_at"] = _now()
            record["last_at"] = record["first_at"]
            record["repeats"] = 0
            self._items.append(record)
            if len(self._items) > MAX_ITEMS:
                del self._items[: len(self._items) - MAX_ITEMS]
            self.save()
            return True

    def mark_read(self, payload: Dict[str, Any]) -> None:
        key = self._key(payload) if isinstance(payload, dict) else None
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if i.get("_key") != key]
            if len(self._items) != before:
                self.save()

    def mark_all_read(self) -> None:
        with self._lock:
            if self._items or self._seen_total:
                self._items.clear()
                self._seen_total.clear()
                self.save()

    # ── Reading ───────────────────────────────────────────────────────────

    @property
    def items(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._items)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    @staticmethod
    def _bucket(item: Dict[str, Any]) -> "tuple[str, int]":
        """Which bucket one unread item belongs in, and how much it counts for.

        Distinct items, not arrivals. A level-triggered alert re-fires every poll while its
        condition holds, so counting arrivals would report an hour away from the desk as sixty
        message alerts when there were only ever three unread messages.

        Message alerts are the one place the item is worth more than one: the alert states its
        own number, so "3 unread user message(s) pending" counts as three, split by sender.
        """
        category = str(item.get("category") or "other")
        body = str(item.get("body") or "")
        if category != "messages":
            return category, 1

        match = _LEADING_COUNT.search(body)
        count = int(match.group(1)) if match else 1
        lowered = body.lower()
        if "user message" in lowered:
            return "user messages", count
        if "alliance message" in lowered:
            return "alliance messages", count
        return "messages", count

    def counts(self) -> Dict[str, int]:
        """What was missed, per bucket. See ``_bucket`` for why this is not arrivals."""
        out: Dict[str, int] = {}
        for item in self.items:
            bucket, count = self._bucket(item)
            out[bucket] = out.get(bucket, 0) + count
        return out

    def summary_line(self) -> str:
        """A plain reading of what was missed, for when there is no model to phrase it.

        Reads as "2 missed reports, 3 missed user messages and 1 market alert" -- the shape
        asked for, without the brackets, because this is spoken as often as it is shown.
        """
        counts = self.counts()
        if not counts:
            return ""
        parts = []
        for bucket, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            singular, plural = _BUCKET_WORDS.get(bucket, (bucket, bucket))
            parts.append(f"{count} {singular if count == 1 else plural}")
        if len(parts) == 1:
            return parts[0]
        return ", ".join(parts[:-1]) + " and " + parts[-1]

    def describe_for_prompt(self, limit: int = 8) -> str:
        """The unread notifications as context for the model to speak from.

        Titles and bodies rather than counts, because "someone is bidding on copper" is worth
        more to the listener than "1 market alert" -- but capped, so a long absence does not
        turn into a recital.
        """
        items = self.items
        if not items:
            return ""
        lines = [f"Unread while the user was away ({self.summary_line()}):"]
        for item in items[-limit:]:
            title = str(item.get("title") or "").strip()
            body = " ".join(str(item.get("body") or "").split())
            if len(body) > 200:
                body = body[:197].rstrip() + "..."
            repeats = int(item.get("repeats", 0))
            again = f" (repeated {repeats}x)" if repeats else ""
            lines.append(f"- {title}{again}: {body}" if body else f"- {title}{again}")
        if len(items) > limit:
            lines.append(f"- ...and {len(items) - limit} more")
        return "\n".join(lines)
