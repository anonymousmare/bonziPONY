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
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

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
        # Only the three Reading fields carry a per-tick delta and know how to render it.
        # Government and economy are plain strings; GDP and funds are plain ints.
        return "\n".join([
            "Nation status:",
            f"  Government: {status.government}",
            f"  Economy: {status.economy}",
            f"  Satisfaction: {status.satisfaction.display()}",
            f"  Solar Empire relation: {status.se.display()}",
            f"  New Lunar Republic relation: {status.nlr.display()}",
            f"  GDP last turn: {status.gdp:,} bits per tick",
            f"  Funds: {status.funds:,} bits",
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

    def _forces_line(forces, hostile: bool) -> str:
        rows = [f for f in forces if bool(f.get("hostile")) == hostile]
        if not rows:
            return "  none"
        return "\n".join(
            f"  {f['size']} {f['type']} ({f['weapon']}/{f['armor']}, training {f['training']})"
            for f in rows
        )

    def _render_nation(entry: Dict[str, Any], when: str = "") -> str:
        lines = [
            f"{entry['name']} (nation #{entry['nation_id']}){when}",
            f"  {entry.get('region', '?')}, {entry.get('government', '?')} / "
            f"{entry.get('economy', '?')}, led by {entry.get('leader', '?')}",
            f"  alliance: {entry.get('alliance_name') or 'none'}",
            f"  GDP {entry.get('gdp', 0):,} every 2 hours, age {entry.get('age', 0)}",
        ]
        buildings = entry.get("buildings") or {}
        if buildings:
            top = sorted(buildings.items(), key=lambda kv: -kv[1])
            lines.append("  buildings: " + ", ".join(f"{n} x{c}" for n, c in top))
        economy = entry.get("economy_rows") or {}
        if economy:
            short = [f"{g} {net:+d}" for g, (_gen, _used, net) in
                     sorted(economy.items(), key=lambda kv: kv[1][2])]
            lines.append("  net per tick: " + ", ".join(short))
            lines.append("  (the game's own figures, government upkeep included)")
        forces = entry.get("forces") or []
        lines.append("  defending:")
        lines.append(_forces_line(forces, hostile=False))
        attacking = [f for f in forces if f.get("hostile")]
        if attacking:
            lines.append("  being attacked by:")
            lines.append(_forces_line(forces, hostile=True))
        lines.append(
            "  To simulate against them, put their defenders in a [WARCALC:...] as "
            "'count Type/Weapon/Armour/training'."
        )
        return "\n".join(lines)

    def get_nation(target: str) -> str:
        """One nation's page, by id or by a name already on file."""
        from core import clop_dossier

        dossier = clop_dossier.store()
        wanted = str(target).strip()

        nation_id = None
        if wanted.lstrip("#").isdigit():
            nation_id = int(wanted.lstrip("#"))
        elif dossier is not None:
            known = dossier.find_by_name(wanted)
            if known is None:
                raise ToolError(
                    f"no nation called {wanted!r} on file. Give a nation id, or use "
                    f"[LOOKUP:dossier] to see who is."
                )
            nation_id = int(known["nation_id"])
        if nation_id is None:
            raise ToolError(f"give a nation id, not {wanted!r}")

        # A recent reading is worth more than a page fetch: garrisons only change on war
        # ticks, twelve hours apart.
        if dossier is not None and not dossier.is_stale(nation_id):
            return _render_nation(dossier.nation(nation_id), when=" (from earlier today)")

        nation = bridge.nation(nation_id)
        if dossier is not None:
            dossier.record_nation(nation)
            return _render_nation(dossier.nation(nation_id))
        return _render_nation({
            "nation_id": nation_id, "name": nation.name, "region": nation.region,
            "government": nation.government, "economy": nation.economy,
            "leader": nation.leader, "alliance_name": nation.alliance_name,
            "gdp": nation.gdp, "age": nation.age, "buildings": dict(nation.buildings),
            "economy_rows": {k: list(v) for k, v in nation.economy_rows.items()},
            "forces": [{"name": f.name, "type": f.type, "size": f.size,
                        "training": f.training, "weapon": f.weapon,
                        "armor": f.armor, "hostile": f.hostile} for f in nation.forces],
        })

    def get_alliance(alliance_id: str) -> str:
        if not str(alliance_id).lstrip("#").isdigit():
            raise ToolError(f"give an alliance id, not {alliance_id!r}")
        from core import clop_dossier

        alliance = bridge.alliance(int(str(alliance_id).lstrip("#")))
        clop_dossier.store().record_alliance(alliance)
        lines = [f"{alliance.name} (alliance #{alliance.alliance_id})"]
        lines.append(f"  members: {', '.join(alliance.members) or 'none'}")
        if alliance.in_stasis:
            lines.append(f"  in stasis (cannot act): {', '.join(alliance.in_stasis)}")
        if alliance.nations:
            lines.append("  nations: " + ", ".join(
                f"{n} (#{i}, {r})" for n, i, r in alliance.nations))
        if alliance.economy_rows:
            short = [f"{g} {net:+d}" for g, (_gen, _used, net) in
                     sorted(alliance.economy_rows.items(), key=lambda kv: kv[1][2])]
            lines.append("  combined net per tick: " + ", ".join(short))
        return "\n".join(lines)

    def get_messages() -> str:
        rows = bridge.messages()
        if not rows:
            return "Your inbox is empty."
        lines = [f"Inbox, {len(rows)} message(s), newest first:"]
        for message in rows[:15]:
            lines.append(f"  [{message.posted}] {message.sender}: {message.body}")
        if len(rows) > 15:
            lines.append(f"  ...and {len(rows) - 15} older")
        return "\n".join(lines)

    def get_alliance_messages() -> str:
        rows = bridge.alliance_messages()
        if not rows:
            return "No alliance messages."
        lines = [f"Alliance chat, {len(rows)} message(s), newest first:"]
        for message in rows[:15]:
            lines.append(f"  [{message.posted}] {message.sender}: {message.body}")
        if len(rows) > 15:
            lines.append(f"  ...and {len(rows) - 15} older")
        lines.append("  (reading these marked them read for the account)")
        return "\n".join(lines)

    def get_news() -> str:
        rows = bridge.news()
        if not rows:
            return "No news."
        lines = [f"News, {len(rows)} item(s), newest first:"]
        for item in rows[:12]:
            lines.append(f"  [{item.posted}] {item.message}")
        if len(rows) > 12:
            lines.append(f"  ...and {len(rows) - 12} older")
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

    tools = {
        "get_stockpiles": get_stockpiles,
        "get_nation_status": get_nation_status,
        "get_market": get_market,
        "read_thread": read_thread,
        "get_nation": get_nation,
        "get_alliance": get_alliance,
        "get_messages": get_messages,
        "get_news": get_news,
    }
    # Alliance chat marks itself read for the account, so it is only offered when the
    # user has said that trade is worth making.
    if getattr(getattr(bridge, "config", None), "read_alliance_messages", False):
        tools["get_alliance_messages"] = get_alliance_messages
    return tools


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




def get_dossier() -> str:
    """What she has already read about other nations, and how fresh each reading is.

    Static rather than live on purpose: the dossier is a file, so this still answers when
    the game is unreachable -- which is exactly when knowing what she already learned is
    most useful.
    """
    from core import clop_dossier

    return clop_dossier.store().summary()


# ── The lookup table ──────────────────────────────────────────────────────────
#
# One row per thing she can look up. This drives three things at once -- the
# dispatcher, the prompt block she reads, and the tests -- so the instructions in
# her prompt cannot drift from what actually exists.
#
# There is no API tool-calling schema here on purpose. She asks for things with a
# [LOOKUP:...] tag, the same way she already asks for [DESKTOP:...] and [ACTION:...],
# because that is the one mechanism that works identically on every backend. Native
# function calling would not: DeepSeek intermittently emits tool calls as plain text
# in the content field rather than the structured field, and a custom OpenAI-compatible
# base_url is indistinguishable from a real OpenAI one, so there is no way to know in
# advance whether a given endpoint honours `tools=`. A tag is just text. It cannot fall
# through to text mode, because text is the mode.


@dataclass(frozen=True)
class Lookup:
    """One lookup: what to call it, what it needs, and how to describe it to her."""

    name: str
    #: Other things she might reasonably write instead of `name`.
    aliases: Tuple[str, ...]
    #: Called with the positional arguments parsed out of the tag.
    run: Callable[..., str]
    #: Argument names in order, for the prompt. A trailing "?" marks it optional.
    args: Tuple[str, ...]
    #: One line, shown to her in the prompt block.
    help: str
    #: True when it needs the authenticated session, so it is only offered when
    #: the bridge is connected.
    live: bool = False
    #: For a live lookup, the key it has in ``make_live_tools``. On the row rather than in
    #: a lookup table somewhere else: this used to be a hardcoded dict in ToolRegistry, and
    #: a row missing from it raised KeyError at call time instead of failing at review time.
    #: test_lookup_reachability.py holds every live row to resolving.
    live_name: str = ""


LOOKUPS: Tuple[Lookup, ...] = (
    Lookup("building", ("build",), get_building, ("name",),
           "what a building costs to build and what it does per tick"),
    Lookup("buildings", ("list",), list_buildings, ("produces?", "consumes?", "region?"),
           "which buildings make or eat a thing"),
    Lookup("good", ("resource",), get_good, ("name",),
           "what produces a good, what eats it, what it is needed to build"),
    Lookup("pollution", (), calc_pollution, ("building", "count", "env_facilities?"),
           "the satisfaction cost of owning N of something"),
    Lookup("rules", ("rule",), get_rules, ("topic?",),
           "how ticks, production, pollution, GDP, combat or the market actually work"),
    Lookup("nationtypes", ("governments", "regions"), get_nation_types, (),
           "what each nation TYPE produces, and the government multipliers and caps"),
    Lookup("cost", ("forcecost", "army"), calc_force_cost, ("forces",),
           "what raising and equipping an army costs"),
    Lookup("stockpiles", ("stock", "holding"), None, (),
           "what the user is holding right now", live=True, live_name="get_stockpiles"),
    Lookup("status", ("nation_status", "empire"), None, (),
           "the user's government, economy, satisfaction, funds and standing",
           live=True, live_name="get_nation_status"),
    Lookup("market", ("bids", "orders"), None, ("good?",),
           "who is bidding on what, at what price", live=True, live_name="get_market"),
    Lookup("thread", ("4chan", "posts"), None, ("since_post?",),
           "the 4CLOP thread on /mlp/", live=True, live_name="read_thread"),
    Lookup("nation", ("player", "enemy"), None, ("id_or_name",),
           "another nation: buildings, garrison, GDP and net production per tick",
           live=True, live_name="get_nation"),
    Lookup("alliance", ("bloc",), None, ("id",),
           "an alliance: members, their nations, and the combined economy",
           live=True, live_name="get_alliance"),
    Lookup("messages", ("inbox", "dms"), None, (),
           "the user's inbox", live=True, live_name="get_messages"),
    Lookup("alliance_messages", ("alliance_chat",), None, (),
           "the alliance chat (reading it marks it read for the account)",
           live=True, live_name="get_alliance_messages"),
    Lookup("news", ("headlines",), None, (),
           "the game's news feed", live=True, live_name="get_news"),
    # Not live: the dossier is a file, so it still answers when the game is unreachable --
    # which is exactly when knowing what she already learned is most useful.
    Lookup("dossier", ("intel", "who"), get_dossier, (),
           "which nations she has read, and how fresh each reading is"),
)

BY_NAME: Dict[str, Lookup] = {}
for _lookup in LOOKUPS:
    BY_NAME[_lookup.name] = _lookup
    for _alias in _lookup.aliases:
        BY_NAME[_alias] = _lookup
del _lookup


def _coerce(value: str, name: str):
    """Tag arguments arrive as strings; a few lookups want numbers."""
    if name.rstrip("?") in ("count", "env_facilities", "since_post"):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            raise ToolError(f"{name.rstrip('?')} must be a whole number, got {value!r}")
    return value


class ToolRegistry:
    """What she can look up right now, and how to run one.

    Live lookups are only registered when the bridge is connected. Offering one that
    cannot work would earn a confident answer built on an error string, which is worse
    than her saying she cannot see the game at the moment.
    """

    def __init__(self, bridge=None) -> None:
        self.bridge = bridge
        self._live: Dict[str, Callable[..., str]] = {}
        if bridge is not None and getattr(bridge, "available", False):
            self._live = make_live_tools(bridge)

        self.available: Tuple[Lookup, ...] = tuple(
            l for l in LOOKUPS if not l.live or l.live_name in self._live
        )

    @property
    def names(self) -> List[str]:
        return [l.name for l in self.available]

    def _callable(self, lookup: Lookup) -> Optional[Callable[..., str]]:
        if not lookup.live:
            return lookup.run
        # Live tools are built per-bridge, so the row names the one it wants.
        return self._live.get(lookup.live_name)

    def dispatch(self, query: str) -> str:
        """Run one `[LOOKUP:...]` body. Errors come back as text, never raised.

        Handing the error to her rather than raising it is deliberate: a model that
        asked for a building that does not exist usually recovers by asking for the
        right one, and a traceback here would cost the whole turn.
        """
        parts = [p.strip() for p in str(query).split(":")]
        if not parts or not parts[0]:
            return f"error: empty lookup. Try one of: {', '.join(self.names)}"

        head = parts[0].casefold()
        lookup = BY_NAME.get(head)

        if lookup is None:
            # No kind given, so treat the whole thing as a name and find it. This is
            # the forgiving path: [LOOKUP:Coffee Farm] should just work.
            return self._by_name_only(query)

        if lookup not in self.available:
            return (
                f"error: {lookup.name} needs the live game connection, which is not "
                f"available right now. You can still use: {', '.join(self.names)}"
            )

        run = self._callable(lookup)
        if run is None:
            return f"error: {lookup.name} is not available right now"

        supplied = parts[1:]
        required = [a for a in lookup.args if not a.endswith("?")]
        if len(supplied) < len(required):
            return (
                f"error: {lookup.name} needs {', '.join(lookup.args) or 'no arguments'}. "
                f"Write it as [LOOKUP:{lookup.name}"
                + ("".join(f":{a}" for a in lookup.args) if lookup.args else "")
                + "]"
            )

        try:
            args = [_coerce(v, n) for v, n in zip(supplied, lookup.args)]
            return str(run(*args))
        except ToolError as exc:
            return f"error: {exc}"
        except TypeError as exc:
            return f"error: wrong arguments for {lookup.name}: {exc}"
        except Exception as exc:
            logger.exception("Lookup %s failed", lookup.name)
            return f"error: {lookup.name} failed: {exc}"

    def _by_name_only(self, query: str) -> str:
        """`[LOOKUP:Coffee Farm]` -- work out whether it is a building or a good.

        "Ambiguous" and "absent" are different problems and get different answers. A
        name matching twelve DNA facilities should list them, not claim it does not
        exist -- she can then ask again for the one she meant.
        """
        data = gamedata()
        ambiguous = None
        for rows, run in ((data["buildings"], get_building), (data["goods"], get_good)):
            try:
                _find(rows, query)
            except ToolError as exc:
                if "matches several" in str(exc):
                    ambiguous = exc
                continue
            try:
                return str(run(query))
            except ToolError as exc:
                return f"error: {exc}"
        if ambiguous is not None:
            return f"error: {ambiguous}"
        return (
            f"error: nothing called {query!r}. Name a building or a good, or use one of: "
            f"{', '.join(self.names)}"
        )

    def prompt_block(self) -> str:
        """The instructions she reads, generated from this table.

        Generated rather than written out in the preset so the two cannot disagree --
        adding a lookup here is enough to teach her about it.
        """
        lines = [
            "== LOOKING THINGS UP ==",
            "",
            "You can look up real numbers instead of guessing. Write the tag on its own "
            "and stop -- you will be given the answer and get to speak straight after, so "
            "do not guess in the same breath as asking.",
            "",
            "The looking-up is invisible to the user: they do not see the tag and there is "
            "no pause they notice. So do NOT announce it. Never say 'one sec', 'let me "
            "check' or 'give me a moment' -- write the tag, get the answer, and reply once "
            "with what you found. An announcement with nothing after it is the worst "
            "possible answer.",
            "",
            "These tags are the ONLY way you can reach anything outside this conversation. "
            "You have no web browser and no search. Commands like [BROWSE:...] or "
            "[SEARCH:...] do not exist -- writing one does nothing at all. To read the "
            "/mlp/ thread, use [LOOKUP:thread]; she finds the current thread herself.",
            "",
        ]
        for lookup in self.available:
            args = "".join(f":{a.rstrip('?')}" for a in lookup.args)
            optional = " (arguments after the first are optional)" if any(
                a.endswith("?") for a in lookup.args) else ""
            lines.append(f"  [LOOKUP:{lookup.name}{args}] — {lookup.help}{optional}")
        lines += [
            "  [LOOKUP:<any building or good>] — the short way; works without a kind",
            "  [WARCALC:40 Unicorns/Grid Squares/Shining/12 vs 60 Pegasi/Canopy Lights/Dragon/6]",
            "      — simulate a battle. Each side is 'count Type/Weapon/Armour/training',",
            "      several forces separated by commas, the two sides separated by ' vs '.",
            "",
            "Never invent a cost, a production rate or a battle outcome. Being confidently "
            "wrong about a build cost is worse than taking a second to check. If a reference "
            "block already appears above with what you need, use it and do not look it up again.",
        ]
        return "\n".join(lines)


# ── Warcalc ───────────────────────────────────────────────────────────────────

#: One force, written the way she is asked to write it in the prompt block:
#: "40 Unicorns/Grid Squares/Shining/12" -- count, type, weapon, armour, training.
#: Weapon, armour and training are all optional, so "60 Pegasi" is a valid force of
#: unequipped, untrained pegasi.
_FORCE_RE = re.compile(
    r"^\s*(?P<size>\d+)\s+(?P<type>[A-Za-z]+)\s*"
    r"(?:/\s*(?P<weapon>[^/]*?)\s*)?"
    r"(?:/\s*(?P<armor>[^/]*?)\s*)?"
    r"(?:/\s*(?P<training>\d+)\s*)?$"
)

WARCALC_FORMAT = (
    "[WARCALC:40 Unicorns/Grid Squares/Shining/12 vs 60 Pegasi/Canopy Lights/Dragon/6] "
    "-- each force is 'count Type/Weapon/Armour/training', commas between forces on a "
    "side, ' vs ' between the two sides. Weapon, armour and training may be left off."
)


def parse_force_list(text: str) -> List[Dict[str, Any]]:
    """Parse one side of a WARCALC tag. Raises ToolError naming the format."""
    forces = []
    for chunk in str(text).split(","):
        if not chunk.strip():
            continue
        match = _FORCE_RE.match(chunk)
        if not match:
            raise ToolError(
                f"could not read {chunk.strip()!r} as a force. Format: {WARCALC_FORMAT}"
            )
        force = {
            "name": chunk.strip(),
            "type": match.group("type").strip().title(),
            "size": int(match.group("size")),
            "training": int(match.group("training") or 0),
        }
        for key in ("weapon", "armor"):
            value = (match.group(key) or "").strip()
            if value:
                force[key] = value
        forces.append(force)
    if not forces:
        raise ToolError(f"no forces given. Format: {WARCALC_FORMAT}")
    return forces


def run_warcalc_tag(body: str) -> str:
    """Run a `[WARCALC:... vs ...]` tag body and report the result.

    A parse failure comes back as the expected format rather than as an exception, so
    she can correct herself on the next round rather than losing the turn.
    """
    text = str(body)
    parts = re.split(r"\s+vs\.?\s+", text, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return (
            f"error: needs two sides separated by ' vs '. Format: {WARCALC_FORMAT}"
        )
    try:
        attackers = parse_force_list(parts[0])
        defenders = parse_force_list(parts[1])
    except ToolError as exc:
        return f"error: {exc}"

    # The defender bonus is on unless the attacker is told otherwise, because a
    # defender is normally on their own soil -- that is what "defending" means here.
    bonus = "nobonus" not in text.casefold() and "no bonus" not in text.casefold()
    try:
        return run_warcalc(attackers, defenders, bonus)
    except Exception as exc:
        logger.exception("Warcalc failed")
        return f"error: {exc}"
