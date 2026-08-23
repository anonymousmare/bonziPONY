#!/usr/bin/env python3
"""Reading a whole 4chan thread, and rendering it small enough to be worth reading.

The monitor's alert path only ever wants the newest post, and ``ClopClient._latest_fourchan_post``
serves that. This module is for the other question -- "what has the thread been saying?" -- which
needs every post, the subject, the reply structure, and a rendering that does not cost a fortune
to hand to a language model.

Two things make the rendering small:

* **Renumbering.** A thread's post numbers are nine digits each and appear again inside every
  quote-link. Numbering posts 1..N and rewriting ``>>43381621`` to ``>>3`` costs nothing in
  meaning and takes a large bite out of the length. ``ThreadPost.number`` keeps the real number
  so a caller can still build a link.
* **Keeping line breaks.** ``parse_fourchan_comment`` collapses a post to one line, which is right
  for a toast body. It is wrong here: greentext is a line-level convention, and a post whose
  ``>`` lines have run together reads as gibberish.

Nothing in this module touches the network. ``ClopClient.fourchan_thread_posts`` does the fetch
and hands the decoded payload here, the same way ``overview.py`` parses pages it never requests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Dict, List, Optional, Sequence, Tuple

#: A quote-link as it survives into the plain text: ">>" and the post number it points at.
QUOTELINK_RE = re.compile(r">>(\d+)")


class _CommentLineParser(HTMLParser):
    """A comment fragment as plain text, with ``<br>`` kept as a line break."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        del attrs
        if tag.lower() == "br":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def parse_comment_lines(fragment: str) -> str:
    """A post's comment as plain text over several lines.

    Whitespace is collapsed within each line but never across them, so greentext stays greentext.
    Runs of blank lines collapse to one -- posts padded out with empty lines are common and the
    padding carries nothing.
    """
    parser = _CommentLineParser()
    parser.feed(fragment)
    lines = [" ".join(line.split()) for line in "".join(parser.parts).split("\n")]

    out: List[str] = []
    for line in lines:
        if not line and out and not out[-1]:
            continue
        out.append(line)
    return "\n".join(out).strip()


@dataclass(frozen=True)
class ThreadPost:
    """One post, as read for comprehension rather than for alerting.

    Distinct from ``clop_monitor.FourChanPost``, which is the alert path's record and is written
    into the state file -- that one has a persisted shape worth leaving alone, and no use for a
    subject or a sequence number.
    """

    #: 1 for the opening post, then 2, 3, ... in thread order. What the rendering shows.
    sequence: int
    #: The real post number, for building a link back.
    number: int
    name: str
    #: Unix seconds, as 4chan reports them.
    posted_at: int
    body: str
    #: Only the opening post normally has one.
    subject: str = ""
    #: Real post numbers this post quotes, in the order they appear.
    quotes: Tuple[int, ...] = ()

    def url(self, thread_url: str) -> str:
        return f"{thread_url}#p{self.number}"


class ThreadParseError(RuntimeError):
    """The thread JSON was not shaped the way the API documents."""


def _post_body(raw: Dict[str, object]) -> str:
    """A post's text, or a stand-in naming its image when it has no text at all."""
    body = parse_comment_lines(str(raw.get("com", "")))
    if body:
        return body
    filename = str(raw.get("filename", "")).strip()
    extension = str(raw.get("ext", "")).strip()
    return f"[image: {filename}{extension}]" if filename else "[no text]"


def parse_thread_payload(payload: object) -> List[ThreadPost]:
    """Every post in a decoded ``a.4cdn.org`` thread response, in thread order.

    Posts that cannot be read are skipped rather than fatal: one malformed post in a 400-post
    thread should not cost the reader the other 399. A payload that is not a thread at all still
    raises, because that is a configuration problem and silence would hide it.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("posts"), list):
        raise ThreadParseError("thread response has no posts list")

    posts: List[ThreadPost] = []
    sequence = 0
    for raw in payload["posts"]:
        if not isinstance(raw, dict):
            continue
        try:
            number = int(raw["no"])
            posted_at = int(raw["time"])
        except (KeyError, TypeError, ValueError):
            continue
        sequence += 1
        body = _post_body(raw)
        posts.append(
            ThreadPost(
                sequence=sequence,
                number=number,
                name=str(raw.get("name", "Anonymous")),
                posted_at=posted_at,
                body=body,
                subject=str(raw.get("sub", "")).strip(),
                quotes=tuple(int(n) for n in QUOTELINK_RE.findall(body)),
            )
        )

    if not posts:
        raise ThreadParseError("thread response has no readable posts")
    return posts


def is_archived(payload: object) -> bool:
    """Whether the thread has been archived and can no longer receive posts."""
    if not isinstance(payload, dict):
        return False
    posts = payload.get("posts")
    if not isinstance(posts, list) or not posts or not isinstance(posts[0], dict):
        return False
    return posts[0].get("archived") == 1


def _renumber(body: str, by_number: Dict[int, int]) -> str:
    """Rewrite quote-links from real post numbers to sequence numbers.

    A link to a post in another thread has no sequence number here, so it is left exactly as it
    was rather than guessed at.
    """
    def swap(match: "re.Match[str]") -> str:
        sequence = by_number.get(int(match.group(1)))
        return f">>{sequence}" if sequence is not None else match.group(0)

    return QUOTELINK_RE.sub(swap, body)


def render_thread_compact(
    posts: Sequence[ThreadPost],
    since_number: Optional[int] = None,
    board: str = "",
    thread_id: Optional[int] = None,
) -> str:
    """The thread as compact text.

    ``since_number`` renders only posts newer than that real post number, which is what the
    hourly check wants -- but the renumbering is always computed over the *whole* thread, so a
    reply reading ``>>3`` means the same thing whether or not post 3 was rendered this time. A
    reference to a post the reader cannot see still tells them it is a reply to something
    earlier, which is worth more than an unresolvable nine-digit number.
    """
    by_number = {post.number: post.sequence for post in posts}
    shown = [p for p in posts if since_number is None or p.number > since_number]

    where = f"/{board}/" if board else "thread"
    header = f"{where} {thread_id if thread_id is not None else ''}".strip()
    lines = [f"{header} - {len(posts)} posts total, {len(shown)} shown".lstrip(" -")]

    if not shown:
        lines.append("(no new posts)")
        return "\n".join(lines)

    for post in shown:
        stamp = datetime.fromtimestamp(post.posted_at, timezone.utc).strftime("%m-%d %H:%M")
        head = f"\n#{post.sequence} {post.name} {stamp}"
        if post.subject:
            head += f" | {post.subject}"
        lines.append(head)
        lines.append(_renumber(post.body, by_number))

    return "\n".join(lines)
