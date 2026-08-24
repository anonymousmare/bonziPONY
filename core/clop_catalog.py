"""Find the current 4CLOP thread on /mlp/ rather than being told where it is.

A 4chan thread archives within a day or two. A URL written into a settings file is
therefore wrong most of the time, and being wrong here is silent: she simply reports that
no thread is configured, which reads as "she cannot check the thread" rather than as
"the thread moved". So she looks it up.

The catalog endpoint (``https://a.4cdn.org/<board>/catalog.json``) lists every live thread
on the board with its subject and opening post. Scoring those is enough to pick the
general out reliably, because the OP of the real one links the game itself -- and a link
to the game's own domain is something a thread about pony pornography does not have.

Read-only, stdlib only, and it never posts. Nothing here writes to the board.
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

CATALOG_URL = "https://a.4cdn.org/{board}/catalog.json"

#: 4chan asks for a plain identifying agent and rate-limits hard on the read API.
_USER_AGENT = "bonziPONY-clop-advisor/1.0 (+read-only thread lookup)"
_TIMEOUT_S = 15.0

#: Below this, no thread on the board is convincingly the game's general and she says so
#: rather than reading out whatever scored highest. A wrong thread is worse than none:
#: it would feed her someone else's conversation as though it were her game's politics.
MIN_SCORE = 5

_TAG_RE = re.compile(r"<[^>]+>")


def _text(raw: str) -> str:
    """4chan comments are HTML fragments; flatten one to plain text."""
    return html.unescape(_TAG_RE.sub(" ", raw or ""))


@dataclass(frozen=True)
class CatalogThread:
    """One live thread, as the catalog describes it."""

    number: int
    subject: str
    body: str
    replies: int = 0
    last_modified: int = 0

    def url(self, board: str) -> str:
        return f"https://boards.4chan.org/{board}/thread/{self.number}"


def parse_catalog(payload: object) -> List[CatalogThread]:
    """Every thread in a decoded catalog response, across all its pages."""
    threads: List[CatalogThread] = []
    if not isinstance(payload, list):
        return threads
    for page in payload:
        if not isinstance(page, dict):
            continue
        for row in page.get("threads", []) or []:
            if not isinstance(row, dict) or "no" not in row:
                continue
            try:
                number = int(row["no"])
            except (TypeError, ValueError):
                continue
            threads.append(CatalogThread(
                number=number,
                subject=_text(str(row.get("sub", ""))),
                body=_text(str(row.get("com", ""))),
                replies=int(row.get("replies", 0) or 0),
                last_modified=int(row.get("last_modified", 0) or 0),
            ))
    return threads


def score_thread(thread: CatalogThread, game_domain: str = "4clop.org") -> int:
    """How likely this thread is to be the game's general.

    The domain link is weighted to carry a match on its own because it is the one signal
    that cannot be produced by the board's ordinary use of the word: /mlp/ says "clop"
    constantly and means something else entirely by it.
    """
    subject = thread.subject.casefold()
    body = thread.body.casefold()
    domain = game_domain.casefold().lstrip("w.")

    score = 0
    if domain and domain in body:
        score += 10                      # the OP links the game: near-certain
    if domain and domain in subject:
        score += 10

    # \b so "clopfic" and "clopping" do not count. The general writes ">CLOP:".
    word = re.compile(r"\bclop\b")
    if word.search(subject):
        score += 5
    if word.search(body):
        score += 1

    for phrase in ("clop has risen", "browser game", "previous threads", "reset when"):
        if phrase in body:
            score += 1
    return score


def pick_thread(threads: Sequence[CatalogThread],
                game_domain: str = "4clop.org") -> Optional[CatalogThread]:
    """The best-scoring thread, or None when nothing scores convincingly.

    Ties go to the busier thread: when a new general has just been posted alongside the
    old one, the one people are actually in is the one worth reading.
    """
    ranked = sorted(
        ((score_thread(t, game_domain), t.replies, t) for t in threads),
        key=lambda row: (row[0], row[1]),
        reverse=True,
    )
    if not ranked or ranked[0][0] < MIN_SCORE:
        return None
    return ranked[0][2]


def fetch_catalog(board: str = "mlp", timeout: float = _TIMEOUT_S) -> List[CatalogThread]:
    """Read the board's catalog. Raises OSError/ValueError on failure."""
    request = urllib.request.Request(
        CATALOG_URL.format(board=board),
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return parse_catalog(json.loads(response.read().decode("utf-8", "replace")))


def find_thread(board: str = "mlp", game_domain: str = "4clop.org",
                timeout: float = _TIMEOUT_S) -> Optional[CatalogThread]:
    """The current general on the board, or None if it cannot be found right now.

    Never raises: a board that is unreachable costs her the thread, not the turn.
    """
    try:
        threads = fetch_catalog(board, timeout=timeout)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("Could not read the /%s/ catalog: %s", board, exc)
        return None
    found = pick_thread(threads, game_domain)
    if found is None:
        logger.info("No thread on /%s/ looks like the game's general.", board)
    else:
        logger.info("Found the thread: /%s/%d %r (%d replies)",
                    board, found.number, found.subject, found.replies)
    return found


class ThreadResolver:
    """Remembers which thread she settled on, and re-checks when it goes quiet.

    Held by the bridge so a lookup does not spend a catalog fetch on every question, and
    so the hourly check and an on-demand ``[LOOKUP:thread]`` agree about which thread they
    are talking about.
    """

    def __init__(self, board: str = "mlp", game_domain: str = "4clop.org",
                 recheck_after_s: float = 3600.0) -> None:
        self.board = board
        self.game_domain = game_domain
        self.recheck_after_s = recheck_after_s
        self.current: Optional[CatalogThread] = None
        self._checked_at = 0.0

    def resolve(self, force: bool = False) -> Optional[CatalogThread]:
        """The thread to read, finding one if there is none or the answer has aged out."""
        fresh = (time.monotonic() - self._checked_at) < self.recheck_after_s
        if self.current is not None and fresh and not force:
            return self.current
        found = find_thread(self.board, self.game_domain)
        self._checked_at = time.monotonic()
        if found is not None:
            self.current = found
        return self.current

    def forget(self) -> None:
        """Drop the remembered thread, so the next resolve goes back to the catalog.

        Called when a thread stops returning posts: that is what archiving looks like from
        here, and the answer is to go and find its replacement.
        """
        self.current = None
        self._checked_at = 0.0
