#!/usr/bin/env python3
"""Reading the pages the alert loop never needed: nations, alliances, messages, news.

The monitor polls for *changes* -- how many unread messages, what the newest report says --
so it only ever parsed the few fragments that answer that. An advisor wants to read the pages
themselves: who owns what, who is garrisoned where, what the alliance is arguing about.

Everything here is a pure parser. It is handed HTML that somebody else fetched, exactly like
``overview.py``, and the panel tables are read with ``overview.parse_panel_cells`` rather than
a second implementation -- the game renders "Nation Resources" as an ordinary panel, and that
function already handles the optional icon column and finding a column by its heading.

One page in here is not safe to fetch casually. ``myalliance.php`` runs
``UPDATE users SET alliance_messages_last_checked = NOW()`` (backend_myalliance.php:231), so
loading it marks the alliance chat read in the player's own browser too. That is why the alert
loop refreshes its session by logging in again instead of ever opening it. ``messages.php`` is
different and is safe: ``is_read`` only changes on an explicit POST
(backend_messages.php:108-112), so a plain GET lists the inbox and marks nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

from overview import parse_panel_cells

#: Force types as the game numbers and names them.
FORCE_TYPES = ("Cavalry", "Tanks", "Pegasi", "Unicorns", "Naval", "Alicorns")

#: What the game prints when a unit carries nothing.
SCROUNGED = ("Scrounged Weapons", "Scrounged Armor")

_NUMBER = re.compile(r"-?[\d,]+")
_SIZE = re.compile(r"Size:\s*([\d,]+)", re.IGNORECASE)
_TRAINING = re.compile(r"Training:\s*([\d,]+)", re.IGNORECASE)
_AGE = re.compile(r"Age:\s*([\d,]+)", re.IGNORECASE)
_GDP = re.compile(r"GDP:\s*([\d,]+)", re.IGNORECASE)
_ID_IN_HREF = re.compile(r"[?&](?:nation_id|alliance_id|user_id)=(\d+)")
#: viewalliance.php renders "username (Stasis)" and "Nation (Region)" in one cell each.
_TRAILING_PAREN = re.compile(r"^(?P<head>.*?)\s*\((?P<tail>[^()]*)\)\s*$")


def _split_paren(text: str) -> "Tuple[str, str]":
    """``"Rustlung (Zebrica)"`` -> ``("Rustlung", "Zebrica")``; no parens -> ``(text, "")``."""
    match = _TRAILING_PAREN.match(text or "")
    return (match.group("head"), match.group("tail")) if match else (text, "")


class PageParseError(RuntimeError):
    """The page was not shaped the way the game's template renders it."""


def _int(text: str, default: int = 0) -> int:
    match = _NUMBER.search(text or "")
    return int(match.group(0).replace(",", "")) if match else default


# ── Generic table reading ─────────────────────────────────────────────────────


class _TableParser(HTMLParser):
    """Rows of one table, as lists of cell text, with ``<br>`` kept as a line break.

    ``table_id`` picks a table by its id attribute; without one, every table on the page is
    collected. Line breaks are preserved because the force panels put five separate facts in
    a single cell separated by ``<br/>``, and flattening them would lose the structure.
    """

    def __init__(self, table_id: Optional[str] = None) -> None:
        super().__init__(convert_charrefs=True)
        self._want = table_id
        self._depth = 0
        self._in_cell = False
        self._cell: List[str] = []
        self._row: List[str] = []
        #: Hrefs seen inside the current cell, so a linked id can be recovered.
        self._cell_hrefs: List[str] = []
        self._row_hrefs: List[List[str]] = []
        self.rows: List[List[str]] = []
        self.row_hrefs: List[List[List[str]]] = []

    def handle_starttag(self, tag, attrs) -> None:
        tag = tag.lower()
        attributes = dict(attrs)
        if tag == "table":
            if self._want is None or attributes.get("id") == self._want:
                self._depth += 1
            return
        if not self._depth:
            return
        if tag in ("td", "th"):
            self._in_cell = True
            self._cell = []
            self._cell_hrefs = []
        elif tag == "br" and self._in_cell:
            self._cell.append("\n")
        elif tag == "a" and self._in_cell and attributes.get("href"):
            self._cell_hrefs.append(attributes["href"])

    def handle_endtag(self, tag) -> None:
        tag = tag.lower()
        if not self._depth:
            return
        if tag in ("td", "th"):
            self._row.append(_clean("".join(self._cell)))
            self._row_hrefs.append(list(self._cell_hrefs))
            self._in_cell = False
        elif tag == "tr":
            if self._row:
                self.rows.append(self._row)
                self.row_hrefs.append(self._row_hrefs)
            self._row = []
            self._row_hrefs = []
        elif tag == "table":
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data) -> None:
        if self._in_cell:
            self._cell.append(data)


def _clean(text: str) -> str:
    """Collapse whitespace inside each line but keep the lines apart."""
    lines = [" ".join(line.split()) for line in (text or "").split("\n")]
    return "\n".join(line for line in lines if line)


def table_rows(html: str, table_id: Optional[str] = None) -> List[List[str]]:
    parser = _TableParser(table_id)
    parser.feed(html)
    return parser.rows


def table_rows_with_links(html: str, table_id: Optional[str] = None):
    parser = _TableParser(table_id)
    parser.feed(html)
    return list(zip(parser.rows, parser.row_hrefs))


class _HeadingParser(HTMLParser):
    """The text of every ``<h3>``/``<h4>``/``<h5>`` on the page, in order.

    viewnation.php puts the nation's name, region, government, economy, leader, alliance and
    age in headings rather than in any table, so this is how those are read.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._buf: List[str] = []
        self._hrefs: List[str] = []
        self.headings: List[Tuple[str, List[str]]] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag.lower() in ("h3", "h4", "h5"):
            self._depth += 1
            self._buf = []
            self._hrefs = []
        elif self._depth and tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self._hrefs.append(href)

    def handle_endtag(self, tag) -> None:
        if tag.lower() in ("h3", "h4", "h5") and self._depth:
            self._depth -= 1
            self.headings.append((_clean("".join(self._buf)), list(self._hrefs)))

    def handle_data(self, data) -> None:
        if self._depth:
            self._buf.append(data)


def headings(html: str) -> List[Tuple[str, List[str]]]:
    parser = _HeadingParser()
    parser.feed(html)
    return parser.headings


# ── viewnation.php ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Force:
    """One force garrisoned in or attacking a nation."""

    name: str
    type: str
    size: int
    training: int
    weapon: str = "Scrounged Weapons"
    armor: str = "Scrounged Armor"
    group: str = ""
    owner: str = ""
    hostile: bool = False       # True for the Attackers list

    def as_warcalc(self) -> Dict[str, object]:
        """This force in the shape ``core.warcalc.simulate`` wants.

        The reason the whole nation parser is worth having: "can I take nation 47" stops
        being hypothetical once the defenders can be read off their own page.
        """
        return {
            "name": self.name or self.type,
            "type": self.type,
            "size": self.size,
            "training": self.training,
            "weapon": self.weapon,
            "armor": self.armor,
        }


@dataclass(frozen=True)
class Nation:
    """Everything viewnation.php is willing to say about a nation.

    Note what is absent: satisfaction and stockpiles are the owner's business and are not
    rendered. Buildings, garrison, GDP and the per-tick economy are all public.
    """

    nation_id: Optional[int]
    name: str
    region: str = ""
    government: str = ""
    economy: str = ""
    leader: str = ""
    alliance_id: Optional[int] = None
    alliance_name: str = ""
    age: int = 0
    gdp: int = 0
    buildings: Dict[str, int] = field(default_factory=dict)
    forces: Tuple[Force, ...] = ()
    #: good -> (generated, used, net) per tick, as the game itself computes it --
    #: government upkeep included, which a count of buildings would miss.
    economy_rows: Dict[str, Tuple[int, int, int]] = field(default_factory=dict)

    @property
    def defenders(self) -> Tuple[Force, ...]:
        return tuple(f for f in self.forces if not f.hostile)

    @property
    def attackers(self) -> Tuple[Force, ...]:
        return tuple(f for f in self.forces if f.hostile)

    @property
    def total_defence(self) -> int:
        return sum(f.size for f in self.defenders)


def _parse_forces(html: str) -> Tuple[Force, ...]:
    """Every force panel on a nation page, split into attackers and defenders.

    The page marks the two lists only with an ``<h4>Attackers</h4>`` / ``<h4>Defenders</h4>``
    heading before them, so the split is by position in the document rather than by markup.
    """
    lowered = html.lower()
    attackers_at = lowered.find(">attackers<")
    defenders_at = lowered.find(">defenders<")

    parser = _TableParser(None)
    parser.feed(html)

    forces: List[Force] = []
    for row in parser.rows:
        for cell in row:
            if "Size:" not in cell or "Training:" not in cell:
                continue
            lines = [line for line in cell.split("\n") if line]
            size_match, training_match = _SIZE.search(cell), _TRAINING.search(cell)
            if not (size_match and training_match):
                continue
            # name, type, [weapon, armor], Size:, Training:  -- gear is omitted for
            # alicorns, which the game gives fixed stats regardless of what they carry.
            body = [line for line in lines
                    if not line.startswith("Size:") and not line.startswith("Training:")]
            name = body[0] if body else ""
            force_type = next((line for line in body if line in FORCE_TYPES), "")
            gear = [line for line in body[1:] if line != force_type]
            forces.append(Force(
                name=name,
                type=force_type or "Cavalry",
                size=_int(size_match.group(1)),
                training=_int(training_match.group(1)),
                weapon=gear[0] if len(gear) >= 2 else SCROUNGED[0],
                armor=gear[1] if len(gear) >= 2 else SCROUNGED[1],
            ))
            break

    if attackers_at == -1 or defenders_at == -1:
        return tuple(forces)

    # Re-walk to decide which side each belongs to, by where its cell sits in the source.
    marked: List[Force] = []
    cursor = 0
    for force in forces:
        needle = f"Size: {force.size}"
        at = html.find(needle, cursor)
        if at == -1:
            at = cursor
        cursor = at + 1
        hostile = attackers_at < at < defenders_at if attackers_at < defenders_at else at > attackers_at
        marked.append(Force(
            name=force.name, type=force.type, size=force.size, training=force.training,
            weapon=force.weapon, armor=force.armor, hostile=hostile,
        ))
    return tuple(marked)


def parse_nation(html: str, nation_id: Optional[int] = None) -> Nation:
    """Read a viewnation.php page."""
    found = headings(html)
    if not found:
        raise PageParseError("no headings on the page; is this really viewnation.php?")

    name = found[0][0]
    region = government = economy = leader = alliance_name = ""
    alliance_id = None
    age = 0
    for text, hrefs in found[1:]:
        if text.startswith("Government:"):
            government = text.split(":", 1)[1].strip()
        elif text.startswith("Economy:"):
            economy = text.split(":", 1)[1].strip()
        elif text.startswith("Leader:"):
            leader = text.split(":", 1)[1].strip()
        elif text.startswith("Alliance:"):
            alliance_name = text.split(":", 1)[1].strip()
            for href in hrefs:
                match = _ID_IN_HREF.search(href)
                if match and "alliance_id" in href:
                    alliance_id = int(match.group(1))
        elif text.startswith("Created:"):
            age_match = _AGE.search(text)
            age = _int(age_match.group(1)) if age_match else 0
        elif not region and text not in ("Attackers", "Defenders"):
            region = text

    gdp_match = _GDP.search(html)
    gdp = _int(gdp_match.group(1)) if gdp_match else 0

    buildings: Dict[str, int] = {}
    for row in table_rows(html, table_id="buildings"):
        if len(row) >= 2 and row[0]:
            buildings[row[0]] = _int(row[1])

    economy_rows: Dict[str, Tuple[int, int, int]] = {}
    try:
        for good, cells in parse_panel_cells(html, "Nation Resources"):
            if good == "Resource" or len(cells) < 3:
                continue  # the <thead> row
            economy_rows[good] = (_int(cells[0]), _int(cells[1]), _int(cells[2]))
    except Exception:
        # The panel is only rendered for nations that own something. A nation with no
        # economy is a real answer, not a parse failure.
        pass

    return Nation(
        nation_id=nation_id, name=name, region=region, government=government,
        economy=economy, leader=leader, alliance_id=alliance_id,
        alliance_name=alliance_name, age=age, gdp=gdp, buildings=buildings,
        forces=_parse_forces(html), economy_rows=economy_rows,
    )


# ── viewalliance.php ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Alliance:
    alliance_id: Optional[int]
    name: str
    members: Tuple[str, ...] = ()
    #: Members flagged as being in stasis -- they cannot act, which is worth knowing
    #: before counting them as a threat.
    in_stasis: Tuple[str, ...] = ()
    #: (nation name, nation_id, region) for every nation belonging to a member.
    nations: Tuple[Tuple[str, Optional[int], str], ...] = ()
    #: good -> (generated, used, net) per tick across the whole alliance.
    economy_rows: Dict[str, Tuple[int, int, int]] = field(default_factory=dict)


def parse_alliance(html: str, alliance_id: Optional[int] = None) -> Alliance:
    """Read a viewalliance.php page."""
    found = headings(html)
    name = found[0][0] if found else ""

    members: List[str] = []
    in_stasis: List[str] = []
    nations: List[Tuple[str, Optional[int], str]] = []
    for row, hrefs in table_rows_with_links(html):
        for cell, cell_hrefs in zip(row, hrefs):
            for href in cell_hrefs:
                match = _ID_IN_HREF.search(href)
                if not match:
                    continue
                if "user_id=" in href and cell:
                    who, note = _split_paren(cell)
                    if who and who not in members:
                        members.append(who)
                        if note.strip().lower() == "stasis":
                            in_stasis.append(who)
                elif "nation_id=" in href and cell:
                    who, region = _split_paren(cell)
                    entry = (who, int(match.group(1)), region)
                    if entry not in nations:
                        nations.append(entry)

    economy_rows: Dict[str, Tuple[int, int, int]] = {}
    for heading in ("Alliance Resources", "Nation Resources"):
        try:
            rows = parse_panel_cells(html, heading)
        except Exception:
            continue
        for good, cells in rows:
            if good == "Resource" or len(cells) < 3:
                continue
            economy_rows[good] = (_int(cells[0]), _int(cells[1]), _int(cells[2]))
        if economy_rows:
            break

    return Alliance(alliance_id=alliance_id, name=name, members=tuple(members),
                    in_stasis=tuple(in_stasis), nations=tuple(nations),
                    economy_rows=economy_rows)


# ── messages.php / myalliance.php / news.php ──────────────────────────────────


@dataclass(frozen=True)
class Message:
    body: str
    sender: str
    posted: str


#: messages.php renders the inbox, then this heading, then everything you have sent.
#: Anchored to the <center> heading rather than the bare word: the page also has a
#: <option value="sentbox">Sentbox</option> in a dropdown *above* the inbox, and matching
#: that puts the split before the messages instead of between them.
_SENTBOX_MARKER = re.compile(r"<center>\s*Sentbox\s*</center>", re.IGNORECASE)


def parse_messages(html: str, box: str = "inbox") -> List[Message]:
    """Messages from messages.php -- the inbox by default, or ``box="sentbox"``.

    Both boxes are plain tables on one page with only a heading between them, so the split
    is by position. Without it every reply the player has ever sent comes back as though
    somebody had sent it to them.

    Safe to call: the page lists messages without marking any of them read.
    """
    split = _SENTBOX_MARKER.search(html)
    if split:
        html = html[: split.start()] if box == "inbox" else html[split.end():]
    elif box == "sentbox":
        return []

    out: List[Message] = []
    for row in table_rows(html):
        if len(row) < 3 or row[0] in ("Message", ""):
            continue
        if row[0] == "Message" or row[1] in ("From", "To"):
            continue
        out.append(Message(body=row[0], sender=row[1], posted=row[2]))
    return out


def parse_alliance_messages(html: str) -> List[Message]:
    """The alliance chat from myalliance.php.

    Fetching that page marks the whole alliance chat read for the account, in the player's
    browser as well. The caller is expected to know that; see the module docstring.
    """
    return parse_messages(html)


@dataclass(frozen=True)
class NewsItem:
    message: str
    posted: str


def parse_news(html: str) -> List[NewsItem]:
    """Every row of news.php, newest first as the game orders them."""
    out: List[NewsItem] = []
    for row in table_rows(html):
        if len(row) < 2 or not row[0] or row[0].lower() in ("message", "news"):
            continue
        out.append(NewsItem(message=row[0], posted=row[1]))
    return out
