"""Reading the 4CLOP thread on a schedule, and deciding whether it is worth saying anything.

4CLOP is player-versus-player and cooperative at once, so who is allied with whom, who is
being ganged up on, and what everyone is panicking about is real information about the game.
That is why she reads the thread: not to post in it -- she never posts -- but so that when
she offers a read on your position it is informed by the politics rather than only by your
stockpiles.

The whole design point is the **cheap gate**. Deciding whether to comment is arithmetic on
counts, done before any model is involved:

* no new posts at all -> silent, no call
* only a few new posts and she already said her piece last hour -> silent, no call
* otherwise -> read the new posts and decide, and she may still pass

So a dead thread costs nothing per hour, and a busy one costs one call over only the posts
that are new. Without the gate an hourly check on a quiet thread would be a standing charge
for nothing.

State lives in ``clop_thread_state.json`` so the gate survives a restart -- otherwise every
restart would look like "never checked" and she would comment again on posts she has already
seen.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = Path(__file__).resolve().parent.parent / "clop_thread_state.json"

#: Below this many new posts, having commented last time is reason enough to stay quiet.
QUIET_THRESHOLD = 3

#: What she says instead of speaking, and what we look for to know she chose silence.
PASS_TOKEN = "[PASS]"

#: Post bodies are written by anyone on 4chan. The agent loop already strips bracket
#: expressions out of window titles for exactly this reason -- a tag in untrusted text is
#: read by the same parser that reads hers, so [DESKTOP:BROWSE:...] in a post would
#: otherwise be a free command. Same treatment here.
_BRACKETS = re.compile(r"\[[^\]]*\]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: A single post is capped so one enormous copypasta cannot crowd out the rest of the thread.
MAX_POST_CHARS = 1200


def sanitize(text: str) -> str:
    """Make one post safe to put in a prompt.

    Strips control characters and bracket expressions, and caps the length. This is the same
    defence the agent loop applies to window titles; thread posts are if anything more
    hostile, being written by people who cannot see the reader but can guess at them.
    """
    cleaned = _CONTROL.sub("", text or "")
    cleaned = _BRACKETS.sub("", cleaned)
    if len(cleaned) > MAX_POST_CHARS:
        cleaned = cleaned[:MAX_POST_CHARS].rstrip() + " ..."
    return cleaned


@dataclass
class ThreadState:
    """What the last check saw and did."""

    last_post_number: int = 0
    post_count: int = 0
    commented_last_time: bool = False
    last_comment: str = ""
    last_checked_at: str = ""
    thread_url: str = ""

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "ThreadState":
        path = Path(path or DEFAULT_STATE_PATH)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            logger.warning("Could not read %s (%s) — treating the thread as unseen", path, exc)
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known}) if isinstance(raw, dict) else cls()

    def save(self, path: Optional[Path] = None) -> None:
        path = Path(path or DEFAULT_STATE_PATH)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not write %s: %s", path, exc)


@dataclass
class Decision:
    """Whether to spend a model call on this check, and why."""

    should_read: bool
    reason: str
    new_posts: int


def decide(state: ThreadState, posts: List[Any], thread_url: str = "") -> Decision:
    """The cheap gate. Pure arithmetic -- no model, no network.

    A thread swap resets the reasoning: a brand new thread is worth reading even if the old
    one was quiet, and its post numbers are unrelated to the last one's.
    """
    if not posts:
        return Decision(False, "the thread has no readable posts", 0)

    if thread_url and state.thread_url and thread_url != state.thread_url:
        return Decision(True, "the thread has changed since the last check", len(posts))

    if not state.last_post_number:
        return Decision(True, "first look at this thread", len(posts))

    new_posts = sum(1 for p in posts if p.number > state.last_post_number)
    if new_posts == 0:
        return Decision(False, "nothing new since the last check", 0)
    if new_posts < QUIET_THRESHOLD and state.commented_last_time:
        return Decision(
            False,
            f"only {new_posts} new post(s) and she already commented last time",
            new_posts,
        )
    return Decision(True, f"{new_posts} new post(s) to read", new_posts)


def build_prompt(rendered: str, state: ThreadState, character: str) -> str:
    """Ask her to read the new posts and decide whether any of it is worth mentioning."""
    already = ""
    if state.commented_last_time and state.last_comment:
        already = (
            f"\nLast time you looked, you said this to the user: \"{state.last_comment}\"\n"
            f"Do not repeat it or say a rephrased version of it."
        )
    return (
        f"(You are {character}. You quietly check the 4CLOP thread on /mlp/ every so often "
        f"to keep track of the game's politics — who is allied with whom, who is getting "
        f"ganged up on, what everyone is arguing about. 4CLOP is PvP and co-op at once, so "
        f"this is real information about the user's position.\n\n"
        f"You are NOT posting in the thread. You never post. You are only deciding whether "
        f"to say something to the USER about what you just read.\n\n"
        f"Here are the new posts:\n\n{rendered}\n{already}\n\n"
        f"If something here actually matters to the user — a threat, an alliance shifting, "
        f"a price everyone is talking about, something genuinely funny — say it out loud in "
        f"one or two sentences, in your own voice, like you just looked up from reading.\n\n"
        f"If it is the usual noise, or nothing has changed, reply with exactly {PASS_TOKEN} "
        f"and nothing else. Staying quiet is the right answer most of the time — you are "
        f"not obliged to have a thought about every thread.)"
    )


def render_new_posts(posts: List[Any], state: ThreadState, board: str = "",
                     thread_id: Optional[int] = None) -> str:
    """The new posts, sanitized and compact.

    Only the new ones: the old ones were already read, and paying for them again every hour
    is the cost this whole module exists to avoid.
    """
    import fourchan

    safe = [
        fourchan.ThreadPost(
            sequence=p.sequence,
            number=p.number,
            name=sanitize(p.name),
            posted_at=p.posted_at,
            body=sanitize(p.body),
            subject=sanitize(p.subject),
            quotes=p.quotes,
        )
        for p in posts
    ]
    return fourchan.render_thread_compact(
        safe,
        since_number=state.last_post_number or None,
        board=board,
        thread_id=thread_id,
    )


def record(state: ThreadState, posts: List[Any], comment: str,
           thread_url: str = "", path: Optional[Path] = None) -> ThreadState:
    """Save what this check saw and whether she spoke, for the next gate to read."""
    state.last_post_number = max((p.number for p in posts), default=state.last_post_number)
    state.post_count = len(posts)
    state.commented_last_time = bool(comment)
    state.last_comment = comment or ""
    state.last_checked_at = datetime.now(timezone.utc).isoformat()
    if thread_url:
        state.thread_url = thread_url
    state.save(path)
    return state
