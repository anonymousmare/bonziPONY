"""The tools the character can call to answer questions about 4CLOP.

Two sources, kept apart on purpose:

* **``data/gamedata.json``** -- static content, generated from the game's own SQL by
  ``tools/export_gamedata.py`` in the CLOP checkout. Building costs, per-tick production,
  pollution parameters, unit and equipment stats. This is the whole point of the export: the
  raw tables are misleading enough that a model reading them would get building costs wrong,
  and a model with no source at all would simply invent them.
* **The live game**, through the CLOP bridge's authenticated session. Stockpiles, nation
  status, the market. Only available while the bridge is connected.

Every tool returns a string, because that is what goes back to the model as a tool result.
They read as prose with numbers in it rather than as JSON: the model is going to speak the
answer out loud, and prose survives that better than a nested object.

Nothing here writes to the game. Everything is a read or a calculation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_GAMEDATA = Path(__file__).resolve().parent.parent / "data" / "gamedata.json"

_MAX_LIST = 40


class ToolError(RuntimeError):
    """A tool could not answer. The message goes back to the model, which usually recovers."""


# ── Game data access ──────────────────────────────────────────────────────────

_DATA: Dict[str, Any] = {}


def gamedata(path: Optional[Path] = None) -> Dict[str, Any]:
    key = str(Path(path or DEFAULT_GAMEDATA))
    if key not in _DATA:
        try:
            _DATA[key] = json.loads(Path(key).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ToolError(
                f"No game data at {key}. Regenerate it with 'python3 tools/export_gamedata.py' "
                f"in the CLOP checkout and copy it to data/gamedata.json."
            ) from exc
        except ValueError as exc:
            raise ToolError(f"Game data at {key} is not valid JSON: {exc}") from exc
    return _DATA[key]


def _find(rows: List[Dict[str, Any]], name: str, field: str = "name") -> Dict[str, Any]:
    """Match a row by name, forgiving case, spacing and a missing/extra 'Build ' prefix."""
    wanted = " ".join(str(name).split()).casefold()
    stripped = wanted[len("build "):] if wanted.startswith("build ") else wanted

    for row in rows:
        if str(row.get(field, "")).casefold() in (wanted, stripped):
            return row
    # Then a unique prefix match, so "coffee" finds "Coffee Farm" but an ambiguous
    # stem says so rather than picking one.
    hits = [r for r in rows if str(r.get(field, "")).casefold().startswith(stripped)]
    if len(hits) == 1:
        return hits[0]
    contains = [r for r in rows if stripped in str(r.get(field, "")).casefold()]
    if len(contains) == 1:
        return contains[0]
    if contains:
        names = ", ".join(sorted(str(r.get(field)) for r in contains)[:12])
        raise ToolError(f"{name!r} matches several things: {names}. Ask for one exactly.")
    raise ToolError(f"Nothing called {name!r}. Use list_buildings to see what exists.")


def _amounts(mapping: Dict[str, Any]) -> str:
    if not mapping:
        return "nothing"
    return ", ".join(f"{amount} {good}" for good, amount in mapping.items())


def _money(value: Optional[int]) -> str:
    return "n/a" if value is None else f"{value:,} bits"


# ── Static tools ──────────────────────────────────────────────────────────────

def get_building(name: str) -> str:
    """Everything about one building: what it costs, what it does, what it pollutes."""
    data = gamedata()
    row = _find(data["buildings"], name)
    lines = [f"{row['name']}"]

    for recipe in row.get("built_by", []):
        lines.append(
            f"  Build: {_money(recipe['money_cost'])}"
            + (f" (Przewalskia {_money(recipe['money_cost_przewalskia'])})"
               if recipe.get("money_cost_przewalskia") is not None else "")
        )
        lines.append(f"    consumes {_amounts(recipe['build_consumes'])}")
        if recipe.get("build_requires_owned"):
            lines.append(
                f"    requires you already own {_amounts(recipe['build_requires_owned'])}"
                f" (checked, NOT consumed)"
            )
        if recipe.get("region") and recipe["region"] != "Any":
            where = recipe["region"]
            if recipe.get("subregion") and recipe["subregion"] != "Any":
                where = f"{recipe['subregion']} {where}"
            lines.append(f"    only buildable in {where}")
        if recipe.get("satisfaction_on_build"):
            lines.append(f"    one-time on build: {recipe['satisfaction_on_build']:+d} satisfaction")
        if recipe.get("przewalskia_note"):
            lines.append(f"    {recipe['przewalskia_note']}")
    if not row.get("built_by"):
        lines.append(f"  Build: {row.get('no_recipe_note', 'no build recipe')}")

    lines.append("  Per tick, per building:")
    lines.append(f"    produces {_amounts(row['produces_per_tick'])}")
    lines.append(f"    consumes {_amounts(row['consumes_per_tick'])}")
    if row.get("gdp_per_tick"):
        lines.append(f"    GDP {row['gdp_per_tick']:+,}")
    if row.get("satisfaction_per_tick"):
        lines.append(f"    satisfaction {row['satisfaction_per_tick']:+d}")
    for key, label in (("se_relation_per_tick", "Solar Empire relation"),
                       ("nlr_relation_per_tick", "New Lunar Republic relation")):
        if row.get(key):
            lines.append(f"    {label} {row[key]:+d}")

    pollution = row.get("pollution", {})
    if pollution.get("pollutes"):
        lines.append(
            f"  Pollution: free up to {pollution['bad_min']}, then "
            f"ceil((count - {pollution['bad_min']})^2 / {pollution['bad_div']}) satisfaction "
            f"per tick. Use calc_pollution for a number."
        )
    else:
        lines.append("  Pollution: none.")

    if row.get("satisfaction_on_destroy"):
        lines.append(f"  Recycling one gives {row['satisfaction_on_destroy']:+d} satisfaction.")
    if row.get("description_warning"):
        lines.append(f"  NOTE: {row['description_warning']}")
    lines.append(
        "  Reminder: if any per-tick input is short for the WHOLE stack of this building "
        "type, the entire stack produces nothing that tick and costs 1 satisfaction each."
    )
    return "\n".join(lines)


def list_buildings(produces: str = "", consumes: str = "", region: str = "") -> str:
    """Building names, optionally filtered by what they make, eat, or where they go."""
    data = gamedata()
    rows = data["buildings"]

    def matches(row) -> bool:
        if produces and not any(produces.casefold() in g.casefold()
                                for g in row["produces_per_tick"]):
            return False
        if consumes and not any(consumes.casefold() in g.casefold()
                                for g in row["consumes_per_tick"]):
            return False
        if region:
            regions = {r.get("region", "") for r in row.get("built_by", [])}
            if not any(region.casefold() in r.casefold() for r in regions):
                return False
        return True

    hits = [r for r in rows if matches(r)]
    if not hits:
        return "No buildings match that."
    lines = [f"{len(hits)} building(s):"]
    for row in hits[:_MAX_LIST]:
        makes = _amounts(row["produces_per_tick"])
        eats = _amounts(row["consumes_per_tick"])
        lines.append(f"  {row['name']} — makes {makes}; eats {eats} (per tick, per building)")
    if len(hits) > _MAX_LIST:
        lines.append(f"  ...and {len(hits) - _MAX_LIST} more")
    return "\n".join(lines)


def get_good(name: str) -> str:
    """What makes a good, what eats it, and whether it can be traded.

    Four separate relationships, because the game has four and lumping them together loses
    the distinction that matters. A building can make a good every tick (Coffee Farm ->
    Coffee) or a one-off action can manufacture it (Manufacture Composites -> Composites).
    A building can eat it every tick (a Mall eats Gems) or merely cost it once at
    construction (a Barracks costs Copper). Only the per-tick ones are an ongoing drain.
    """
    data = gamedata()
    row = _find(data["goods"], name)
    good_name = row["name"]

    crafted_by = [a["action_display_name"] for a in data["actions"]
                  if a.get("produces") == good_name]
    build_cost_of = [b["name"] for b in data["buildings"]
                     for r in b.get("built_by", [])
                     if good_name in (r.get("build_consumes") or {})]
    gear_cost_of = [g["name"] for key in ("weapons", "armor") for g in data[key]
                    if good_name in ((g.get("build") or {}).get("consumes") or {})]

    lines = [
        f"{good_name} (resource id {row['resource_id']})",
        f"  Tradeable: {'yes' if row.get('tradeable') else 'no'}",
        f"  Produced per tick by: {', '.join(row['produced_by']) or 'nothing'}",
        f"  Manufactured by: {', '.join(crafted_by) or 'nothing'}",
        f"  Consumed per tick by: {', '.join(row['consumed_by']) or 'nothing'}",
    ]
    if build_cost_of:
        shown = ", ".join(sorted(set(build_cost_of))[:12])
        more = len(set(build_cost_of)) - 12
        lines.append(f"  Needed to build: {shown}" + (f" and {more} more" if more > 0 else ""))
    if gear_cost_of:
        shown = ", ".join(sorted(set(gear_cost_of))[:12])
        more = len(set(gear_cost_of)) - 12
        lines.append(f"  Needed for equipment: {shown}" + (f" and {more} more" if more > 0 else ""))
    if row.get("satisfaction_per_tick_per_unit"):
        lines.append(
            f"  Holding one gives {row['satisfaction_per_tick_per_unit']:+d} satisfaction/tick"
        )
    if row.get("description_warning"):
        lines.append(f"  NOTE: {row['description_warning']}")
    lines.append(
        "  Anything over 50,000 leaks ceil((amount - 50,000) / 500) per tick."
    )
    return "\n".join(lines)


def calc_pollution(building: str, count: int, env_facilities: int = 0) -> str:
    """The satisfaction cost of owning N of a building, and what env facilities save."""
    import math

    data = gamedata()
    row = _find(data["buildings"], building)
    pollution = row.get("pollution", {})
    bad_min = int(pollution.get("bad_min") or 0)
    bad_div = int(pollution.get("bad_div") or 0)
    count = max(0, int(count))
    env = max(0, int(env_facilities))

    if not bad_min or not bad_div:
        return f"{row['name']} does not pollute at any count."
    if count <= bad_min:
        headroom = bad_min - count
        return (
            f"{count} {row['name']} costs nothing — the free allowance is {bad_min}. "
            f"You could build {headroom} more before it starts hurting."
        )

    loss = math.ceil((count - bad_min) ** 2 / bad_div)
    lines = [
        f"{count} {row['name']}: {loss} satisfaction per tick.",
        f"  ceil(({count} - {bad_min})^2 / {bad_div}) = {loss}",
    ]
    if env:
        after = math.ceil(loss * (0.9 ** env))
        lines.append(
            f"  With {env} working environmental facilities: {after} per tick "
            f"({loss - after} refunded). They must have their 5 Energy each to count, and "
            f"the 0.9^n applies to your WHOLE pollution total, not just this building."
        )
    one_more = math.ceil((count + 1 - bad_min) ** 2 / bad_div)
    lines.append(f"  One more would make it {one_more} (+{one_more - loss}).")
    lines.append("  Count is amount minus disabled, and each disabled building costs 1 more.")
    return "\n".join(lines)


def get_rules(topic: str = "") -> str:
    """The tick rules that live in the game's PHP rather than in any table."""
    data = gamedata()
    rules = data["rules"]
    if topic:
        key = next((k for k in rules if topic.casefold() in k.casefold()), None)
        if key is None:
            return f"No rules section called {topic!r}. Have: {', '.join(rules)}."
        return f"{key}:\n" + json.dumps(rules[key], indent=2)
    return "Rules sections: " + ", ".join(rules) + ". Ask for one by name."


def get_nation_types() -> str:
    """What each nation type can and cannot do."""
    data = gamedata()
    lines = ["Nation types:"]
    for name, row in data["nations"].items():
        exclusive = ", ".join(row.get("produces_exclusively") or []) or "nothing exclusive"
        lines.append(f"  {name} (region {row['region_id']}): {exclusive}")
        if row.get("note"):
            lines.append(f"    {row['note']}")
    lines.append("")
    lines.append("Governments (GDP multiplier / satisfaction cap):")
    for name, row in data["governments"].items():
        mult = row.get("gdp_multiplier")
        mult_text = f"x{mult}" if mult else "1 + satisfaction/1000"
        lines.append(f"  {name}: {mult_text}, cap {row['satisfaction_cap']}")
    return "\n".join(lines)


# ── Live tools ────────────────────────────────────────────────────────────────

def make_live_tools(bridge) -> Dict[str, Callable[..., str]]:
    """Tools that need the authenticated session. Empty dict when the bridge is off."""

    def get_stockpiles() -> str:
        stock = {k: v for k, v in bridge.stockpiles().items() if v}
        if not stock:
            return "You are holding nothing at all."
        lines = ["Current stockpiles:"]
        for good, amount in sorted(stock.items(), key=lambda kv: -kv[1]):
            note = ""
            if amount > 50000:
                import math
                note = f"  (leaking {math.ceil((amount - 50000) / 500)}/tick over the 50k cap)"
            lines.append(f"  {good}: {amount:,}{note}")
        return "\n".join(lines)

    def get_nation_status() -> str:
        html = bridge.overview_html()
        import nation as nation_module

        status = nation_module.NationStatus.from_overview(html)
        return "\n".join([
            "Nation status:",
            f"  Government: {status.government.display()}",
            f"  Economy: {status.economy.display()}",
            f"  Satisfaction: {status.satisfaction.display()}",
            f"  Solar Empire relation: {status.se.display()}",
            f"  New Lunar Republic relation: {status.nlr.display()}",
            f"  GDP last turn: {status.gdp.display()}",
            f"  Funds: {status.funds.display()}",
            f"  Server time: {status.server_time}",
        ])

    def get_market(good: str = "") -> str:
        orders = bridge.market_orders()
        if good:
            wanted = good.casefold()
            orders = [o for o in orders if wanted in o.good.casefold()]
        if not orders:
            return (
                f"No pending buy orders{' for ' + good if good else ''}. "
                f"Only goods listed in the monitor's settings.json are watched."
            )
        lines = ["Pending buy orders (what other nations are bidding):"]
        for order in orders:
            lines.append(
                f"  {order.good}: {order.nation_name} ({order.relation_label()}) "
                f"wants {order.amount:,} at {order.price:,} bits each"
            )
        lines.append(
            "  Remember the spread: a buyer pays price x their buy multiplier and the seller "
            "receives price x their sell multiplier; the difference is destroyed."
        )
        return "\n".join(lines)

    def read_thread(since_post: int = 0) -> str:
        import fourchan

        posts = bridge.thread_posts()
        if not posts:
            return "No 4chan thread is configured, or it has no readable posts."
        thread = bridge.client.fourchan_thread
        return fourchan.render_thread_compact(
            posts,
            since_number=int(since_post) or None,
            board=getattr(thread, "board", ""),
            thread_id=getattr(thread, "thread_id", None),
        )

    return {
        "get_stockpiles": get_stockpiles,
        "get_nation_status": get_nation_status,
        "get_market": get_market,
        "read_thread": read_thread,
    }


# ── Warcalc tools ─────────────────────────────────────────────────────────────

def run_warcalc(attackers: List[Dict], defenders: List[Dict],
                defender_bonus: bool = True) -> str:
    """Simulate a battle and report the casualties."""
    from core import warcalc

    result = warcalc.simulate(attackers, defenders, defender_bonus)
    lines = [f"Result: {result['outcome']}."]
    if result["defender_bonus_applied"]:
        lines.append("  Home-defence 0.75 applied to damage against the defenders.")
    for label, rows in (("Attackers", result["attackers"]), ("Defenders", result["defenders"])):
        if not rows:
            continue
        lines.append(f"  {label}:")
        for row in rows:
            fate = "destroyed" if row["destroyed"] else f"{row['remaining']} left"
            lines.append(
                f"    {row['name']} ({row['type']}, {row['weapon']}/{row['armor']}, "
                f"training {row['training']}): {row['size']} -> lost {row['lost']}, {fate}"
            )
    if result["stalls"]:
        lines.append(f"  Stalemates (zero damage): {len(result['stalls'])}")
    return "\n".join(lines)


def calc_force_cost(forces: List[Dict], przewalskia: bool = False) -> str:
    """What raising and equipping a set of forces costs."""
    from core import warcalc

    cost = warcalc.force_cost(forces, przewalskia)
    lines = [
        f"Raising: {_money(cost['hire_cost'])}",
        f"Equipment: {_money(cost['equipment_cost'])}",
        f"Crafting the parts: {_money(cost['crafting_cost'])}",
        f"Total money: {_money(cost['total_money'])}",
        f"Materials as bought: {_amounts(cost['materials_as_bought'])}",
        f"Materials as raw: {_amounts(cost['materials_as_raw'])}",
        f"Upkeep per war tick: {_amounts(cost['upkeep_per_war_tick'])}",
        f"  {cost['upkeep_note']}",
    ]
    return "\n".join(lines)


# ── Schemas and dispatch ──────────────────────────────────────────────────────

def _schema(name: str, description: str, properties: Dict[str, Any],
            required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
        },
    }


_FORCE_ITEM = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "a label for this force"},
        "type": {
            "type": "string",
            "enum": ["Cavalry", "Tanks", "Pegasi", "Unicorns", "Naval", "Alicorns"],
        },
        "size": {"type": "integer", "description": "number of troops"},
        "training": {"type": "integer", "description": "0-20; from Barracks owned"},
        "weapon": {"type": "string", "description": "exact weapon name, or omit for unequipped"},
        "armor": {"type": "string", "description": "exact armour name, or omit for unequipped"},
    },
    "required": ["type", "size"],
}

#: The static tools, available whether or not the bridge is connected.
STATIC_SCHEMAS = [
    _schema(
        "get_building",
        "Full detail on one building: money and material cost to build, what it produces and "
        "consumes per tick, its satisfaction and GDP effect, and its pollution parameters. "
        "Use this rather than answering from memory -- the numbers are exact.",
        {"name": {"type": "string", "description": "e.g. 'Coffee Farm' or 'Build Coffee Farm'"}},
        ["name"],
    ),
    _schema(
        "list_buildings",
        "List buildings, optionally filtered by what they produce, what they consume, or "
        "which nation region they are locked to.",
        {
            "produces": {"type": "string", "description": "good name, e.g. 'Coffee'"},
            "consumes": {"type": "string", "description": "good name, e.g. 'Energy'"},
            "region": {"type": "string",
                       "description": "Saddle Arabia, Zebrica, Burrozil or Przewalskia"},
        },
    ),
    _schema(
        "get_good",
        "What produces a good, what consumes it, and whether it can be traded.",
        {"name": {"type": "string"}},
        ["name"],
    ),
    _schema(
        "calc_pollution",
        "The satisfaction cost per tick of owning N of a building, and what environmental "
        "facilities would save. Pollution is quadratic past a free allowance.",
        {
            "building": {"type": "string"},
            "count": {"type": "integer", "description": "how many you own (amount minus disabled)"},
            "env_facilities": {"type": "integer",
                               "description": "working Solar/Lunar Environmental Facilities"},
        },
        ["building", "count"],
    ),
    _schema(
        "get_rules",
        "The tick rules that exist only in the game's code and no table: production gating, "
        "pollution, GDP, combat, market spread, war ticks, satisfaction penalties.",
        {"topic": {"type": "string",
                   "description": "ticks, production, pollution, gdp, combat, market, "
                                  "stockpile_siphon, satisfaction_penalties"}},
    ),
    _schema(
        "get_nation_types",
        "What each nation type produces exclusively, and the government GDP multipliers and "
        "satisfaction caps.",
        {},
    ),
    _schema(
        "run_warcalc",
        "Simulate a battle and report exactly who dies. This is a port of the game's own "
        "combat loop, so the numbers are what the tick would actually do.",
        {
            "attackers": {"type": "array", "items": _FORCE_ITEM},
            "defenders": {"type": "array", "items": _FORCE_ITEM},
            "defender_bonus": {
                "type": "boolean",
                "description": "true when the defenders are on their own soil and their owner "
                               "is not in stasis; applies a 0.75 multiplier to damage against "
                               "them. Defaults to true.",
            },
        },
        ["attackers", "defenders"],
    ),
    _schema(
        "calc_force_cost",
        "What it costs to raise and equip a set of forces: money, materials as bought, "
        "materials unwound to raw, and upkeep per war tick.",
        {
            "forces": {"type": "array", "items": _FORCE_ITEM},
            "przewalskia": {"type": "boolean",
                            "description": "use Przewalskia's discounted crafting prices"},
        },
        ["forces"],
    ),
]

#: Tools that need the authenticated session.
LIVE_SCHEMAS = [
    _schema("get_stockpiles", "How much of every good the user's nation is holding right now.", {}),
    _schema(
        "get_nation_status",
        "The user's nation right now: government, economy, satisfaction, funds, GDP last "
        "turn, and its standing with the Solar Empire and New Lunar Republic.",
        {},
    ),
    _schema(
        "get_market",
        "Pending buy orders on the market -- what other nations are bidding, at what price, "
        "and whether they are allies or enemies. Only covers goods the monitor watches.",
        {"good": {"type": "string", "description": "narrow to one good; omit for all watched"}},
    ),
    _schema(
        "read_thread",
        "Read the configured 4chan thread. Posts are renumbered 1..N and quote-links rewritten "
        "to match. Pass since_post to see only what is new.",
        {"since_post": {"type": "integer",
                        "description": "real post number; only later posts are returned"}},
    ),
]

STATIC_TOOLS: Dict[str, Callable[..., str]] = {
    "get_building": get_building,
    "list_buildings": list_buildings,
    "get_good": get_good,
    "calc_pollution": calc_pollution,
    "get_rules": get_rules,
    "get_nation_types": get_nation_types,
    "run_warcalc": run_warcalc,
    "calc_force_cost": calc_force_cost,
}


class ToolRegistry:
    """The tools available right now, and how to run one.

    Live tools are only offered when the bridge is connected. Offering a tool that cannot
    work would earn a confident answer built on an error string, which is worse than the
    character saying she cannot see the game at the moment.
    """

    def __init__(self, bridge=None) -> None:
        self.bridge = bridge
        self._tools: Dict[str, Callable[..., str]] = dict(STATIC_TOOLS)
        self.schemas: List[Dict[str, Any]] = list(STATIC_SCHEMAS)
        if bridge is not None and getattr(bridge, "available", False):
            self._tools.update(make_live_tools(bridge))
            self.schemas = self.schemas + list(LIVE_SCHEMAS)

    @property
    def names(self) -> List[str]:
        return sorted(self._tools)

    def dispatch(self, name: str, arguments: Dict[str, Any]) -> str:
        """Run one tool. Errors are returned as text for the model, not raised."""
        tool = self._tools.get(name)
        if tool is None:
            return (
                f"error: no tool called {name!r}. Available: {', '.join(self.names)}"
            )
        try:
            result = tool(**(arguments or {}))
        except ToolError as exc:
            return f"error: {exc}"
        except TypeError as exc:
            return f"error: wrong arguments for {name}: {exc}"
        except Exception as exc:
            logger.exception("Tool %s failed", name)
            return f"error: {name} failed: {exc}"
        return str(result)
