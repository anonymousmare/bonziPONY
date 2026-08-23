"""Battle simulation, ported from the game's own combat loop.

This is a Python port of the JavaScript warcalc, which is itself a port of
``clop/huge-ovipositor-backend.php``, which is a copy of the combat section of
``clop/cron/frequent.php``. Three hops from the original, so the quirks are preserved
deliberately rather than tidied:

* **The indexing is crossed.** The attacker's weapon column is chosen by the *defender's*
  type; the defender's armour column is chosen by the *attacker's* type. Getting this
  backwards is the easiest mistake available here and produces plausible wrong numbers.
* **Armour is a multiplier and lower is better.** 0.35 armour takes 35% of the damage.
* **Scrounged gear is id 0, which PHP treats as falsy**, so it falls through to hardcoded
  0.25 damage / 1.0 armour rather than to a table row.
* **Alicorns are recast as naval** with a flat 10 damage and 0.1 armour, ignoring whatever
  they are carrying.

One thing is deliberately *not* faithful: the PHP spins forever when a matchup does zero
damage, and the JS added an iteration budget to stop the browser hanging. That guard is kept.

Stats come from ``data/gamedata.json`` rather than being hardcoded again, so this cannot
drift from the export. That also fixes two things the JS had wrong -- see ``CRAFT_NOTE``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_GAMEDATA = Path(__file__).resolve().parent.parent / "data" / "gamedata.json"

#: Force type ids, as the game numbers them.
FORCE_TYPES = {"Cavalry": 1, "Tanks": 2, "Pegasi": 3, "Unicorns": 4, "Naval": 5, "Alicorns": 6}
TYPES = {1: "cavalry", 2: "tanks", 3: "pegasi", 4: "unicorns", 5: "naval"}
TYPE_NUMS = (1, 2, 3, 4, 5)

#: What an unequipped unit does and takes. PHP reads weapon/armor id 0 as falsy.
SCROUNGED_DAMAGE = 0.25
SCROUNGED_ARMOR = 1.0

#: Alicorns ignore their gear entirely and fight as naval.
ALICORN_DAMAGE = 10.0
ALICORN_ARMOR = 0.1

#: Damage against a unit defending its own nation, unless its owner is in stasis.
HOME_DEFENCE = 0.75

#: The PHP loops forever on a zero-damage matchup. This is where we give up instead.
ITERATION_BUDGET = 4_000_000

CRAFT_NOTE = (
    "The browser warcalc's CRAFT table has the crafting costs wrong twice over: it uses "
    "Przewalskia's discounted prices (200/300/400/400 bits) rather than the standard ones "
    "(20,000 vehicle / 30,000 machinery / 40,000 precision / 40,000 composites), and it has "
    "machinery and vehicle the wrong way round. It also lists the Alicorn hire price as 0; "
    "it is 2,000,000 per point of size. This module reads gamedata.json instead."
)


class WarcalcError(RuntimeError):
    """The simulation was asked for something it cannot do."""


# ── Game data ─────────────────────────────────────────────────────────────────

@dataclass
class GameData:
    """Combat-relevant slices of gamedata.json, indexed for lookup."""

    weapons: Dict[str, Dict[str, float]]      # name -> {cavalry: dmg, ...}
    armor: Dict[str, Dict[str, float]]        # name -> {cavalry: mult, ...}
    weapon_cost: Dict[str, Dict[str, Any]]    # name -> {money_cost, consumes}
    armor_cost: Dict[str, Dict[str, Any]]
    units: Dict[str, Dict[str, Any]]          # Cavalry -> {hire_cost_per_size, upkeep_per_size}
    craft: Dict[str, Dict[str, Any]]          # "Machinery Parts" -> {money, inputs}
    raw: Dict[str, Any]

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "GameData":
        path = Path(path or DEFAULT_GAMEDATA)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise WarcalcError(
                f"No game data at {path}. Regenerate it with "
                f"'python3 tools/export_gamedata.py' in the CLOP checkout and copy it here."
            ) from exc
        except ValueError as exc:
            raise WarcalcError(f"Game data at {path} is not valid JSON: {exc}") from exc

        weapons = {w["name"]: {k: float(v) for k, v in w["stats"].items()}
                   for w in raw.get("weapons", [])}
        armor = {a["name"]: {k: float(v) for k, v in a["stats"].items()}
                 for a in raw.get("armor", [])}
        weapon_cost = {w["name"]: w.get("build", {}) for w in raw.get("weapons", [])}
        armor_cost = {a["name"]: a.get("build", {}) for a in raw.get("armor", [])}

        # The manufacturing actions, so a raw-materials breakdown can unwind crafted parts.
        craft: Dict[str, Dict[str, Any]] = {}
        for action in raw.get("actions", []):
            produces = action.get("produces")
            if produces and action.get("build_consumes"):
                craft[produces] = {
                    "money_cost": action.get("money_cost", 0),
                    "money_cost_przewalskia": action.get("money_cost_przewalskia"),
                    "inputs": dict(action["build_consumes"]),
                    "per": action.get("produces_amount", 1),
                }

        return cls(weapons, armor, weapon_cost, armor_cost,
                   dict(raw.get("units", {})), craft, raw)


_CACHE: Dict[str, GameData] = {}


def game_data(path: Optional[Path] = None) -> GameData:
    """Load and memoise the game data. It is static content; one read is enough."""
    key = str(Path(path or DEFAULT_GAMEDATA))
    if key not in _CACHE:
        _CACHE[key] = GameData.load(path)
    return _CACHE[key]


# ── Units ─────────────────────────────────────────────────────────────────────

@dataclass
class Unit:
    """One force in the simulation."""

    force_id: int
    name: str
    size: int
    training: int
    type: int                      # 6 (Alicorns) is rewritten to 5 during setup
    declared_type: str
    weapon: str
    armor: str
    is_invader: bool
    dmg: Dict[str, float] = field(default_factory=dict)
    arm: Dict[str, float] = field(default_factory=dict)
    hits: int = 0
    damage: float = 0.0
    lost: int = 0
    remaining: int = 0
    dead: bool = False


def build_unit(spec: Dict[str, Any], force_id: int, is_invader: bool, data: GameData) -> Unit:
    """Turn a plain spec dict into a Unit with its damage and armour tables resolved."""
    declared = str(spec.get("type", "")).strip()
    if declared not in FORCE_TYPES:
        raise WarcalcError(
            f"Unknown force type {declared!r}. Expected one of {', '.join(FORCE_TYPES)}."
        )
    size = int(spec.get("size", 0))
    if size < 0:
        raise WarcalcError(f"Force size cannot be negative (got {size})")

    weapon = str(spec.get("weapon") or "Scrounged Weapons")
    armor = str(spec.get("armor") or "Scrounged Armor")

    unit = Unit(
        force_id=force_id,
        name=("A_" if is_invader else "D_") + str(spec.get("name") or declared),
        size=size,
        training=int(spec.get("training", 0)),
        type=FORCE_TYPES[declared],
        declared_type=declared,
        weapon=weapon,
        armor=armor,
        is_invader=is_invader,
        hits=size,
    )

    if unit.type == 6:
        # "Treat them like naval for now" -- and their gear is ignored entirely.
        unit.type = 5
        for n in TYPE_NUMS:
            unit.dmg[TYPES[n]] = ALICORN_DAMAGE
            unit.arm[TYPES[n]] = ALICORN_ARMOR
        return unit

    weapon_stats = data.weapons.get(weapon)
    armor_stats = data.armor.get(armor)
    for n in TYPE_NUMS:
        target = TYPES[n]
        unit.dmg[target] = (
            float(weapon_stats[target]) if weapon_stats else SCROUNGED_DAMAGE
        )
        unit.arm[target] = (
            float(armor_stats[target]) if armor_stats else SCROUNGED_ARMOR
        )
    return unit


# ── Simulation ────────────────────────────────────────────────────────────────

def _round3(value: float) -> float:
    return round(value + 1e-12, 3)


def _round6(value: float) -> float:
    return round(value + 1e-12, 6)


def _damage_buckets(side: Sequence[Unit]) -> List[Dict[str, Any]]:
    """Group a side by how much damage it does, highest first, then by target type.

    Mirrors the PHP's krsort + array_multisort: within a bucket, the biggest force goes
    first, ties broken by nation id (attackers before defenders).
    """
    buckets: Dict[float, Dict[str, List[Unit]]] = {}
    for unit in side:
        for n in TYPE_NUMS:
            target = TYPES[n]
            buckets.setdefault(unit.dmg[target], {}).setdefault(target, []).append(unit)

    out = []
    for value in sorted(buckets, reverse=True):
        by_type = {}
        for n in TYPE_NUMS:
            target = TYPES[n]
            if target in buckets[value]:
                by_type[target] = sorted(
                    buckets[value][target],
                    key=lambda u: (-u.size, 0 if u.is_invader else 1),
                )
        out.append({"damage": value, "by_type": by_type})
    return out


def _defender_pools(side: Sequence[Unit]) -> Dict[int, Dict[int, List[Unit]]]:
    """Index a side by its own type, then by attacker type, best-armoured first.

    "Best armoured first" means lowest multiplier first, which is the PHP's ascending
    sort -- the units that take the least damage soak the hits first.
    """
    pools: Dict[int, Dict[int, List[Unit]]] = {}
    for unit in side:
        for n in TYPE_NUMS:
            pools.setdefault(unit.type, {}).setdefault(n, []).append(unit)
    for own_type in pools:
        for n in TYPE_NUMS:
            if n in pools[own_type]:
                pools[own_type][n] = sorted(
                    pools[own_type][n],
                    key=lambda u, n=n: (u.arm[TYPES[n]], -u.size, 0 if u.is_invader else 1),
                )
    return pools


def simulate(
    attackers: Sequence[Dict[str, Any]],
    defenders: Sequence[Dict[str, Any]],
    defender_bonus: bool = True,
    data: Optional[GameData] = None,
) -> Dict[str, Any]:
    """Fight one battle and report the casualties.

    ``attackers`` and ``defenders`` are lists of
    ``{"type", "size", "training", "weapon", "armor", "name"}``. ``defender_bonus`` applies
    the 0.75 home-defence multiplier, which is on unless the defending nation's owner is in
    stasis.
    """
    data = data or game_data()

    units: Dict[int, Unit] = {}
    invaders: List[Unit] = []
    repellers: List[Unit] = []
    force_id = 0

    for spec in attackers:
        force_id += 1
        unit = build_unit(spec, force_id, True, data)
        units[unit.force_id] = unit
        invaders.append(unit)
    for spec in defenders:
        force_id += 1
        unit = build_unit(spec, force_id, False, data)
        units[unit.force_id] = unit
        repellers.append(unit)

    stalls: List[str] = []
    budget = ITERATION_BUDGET

    def run_side(buckets, enemy_pools, apply_bonus: bool) -> None:
        nonlocal budget
        for bucket in buckets:
            for tn in TYPE_NUMS:
                target = TYPES[tn]
                attacker_list = bucket["by_type"].get(target)
                if not attacker_list:
                    continue

                break_type_loop = False
                for attacker in attacker_list:
                    A = units[attacker.force_id]
                    while A.hits > 0:
                        fought = False
                        pool = enemy_pools.get(tn, {}).get(A.type)
                        if not pool:
                            break

                        out_of_hits = False
                        for defender in pool:
                            D = units[defender.force_id]
                            # Crossed on purpose: the attacker's damage was bucketed by the
                            # DEFENDER's type, and the defender's armour is read against the
                            # ATTACKER's type.
                            dmg = _round3(
                                bucket["damage"]
                                * D.arm[TYPES[A.type]]
                                * 1.5 ** ((A.training - D.training) / 20)
                            )
                            if apply_bonus:
                                dmg *= HOME_DEFENCE

                            if dmg <= 0:
                                stalls.append(f"{A.name} does 0 damage to {D.name} — skipped")
                                continue

                            while D.size > D.damage:
                                fought = True
                                D.damage += dmg
                                A.hits -= 1
                                budget -= 1
                                if budget <= 0:
                                    raise WarcalcError(
                                        "Iteration limit reached — check for zero or "
                                        "near-zero damage values."
                                    )
                                if A.hits == 0:
                                    out_of_hits = True
                                    break
                            if out_of_hits:
                                break
                        if out_of_hits:
                            break
                        if not fought:
                            break_type_loop = True
                            break
                    if break_type_loop:
                        break

    if repellers:
        run_side(_damage_buckets(invaders), _defender_pools(repellers), bool(defender_bonus))
        run_side(_damage_buckets(repellers), _defender_pools(invaders), False)

    for unit in units.values():
        dealt = int(_round6(unit.damage))
        unit.lost = 0
        unit.dead = False
        if dealt > 0:
            if dealt < unit.size:
                unit.lost = dealt
                unit.remaining = unit.size - dealt
            else:
                unit.lost = unit.size
                unit.remaining = 0
                unit.dead = True
        else:
            unit.remaining = unit.size

    def describe(side: Sequence[Unit]) -> List[Dict[str, Any]]:
        return [
            {
                "name": u.name[2:],
                "type": u.declared_type,
                "weapon": u.weapon,
                "armor": u.armor,
                "training": u.training,
                "size": u.size,
                "lost": u.lost,
                "remaining": u.remaining,
                "destroyed": u.dead,
            }
            for u in side
        ]

    attacker_rows = describe(invaders)
    defender_rows = describe(repellers)
    attacker_left = sum(r["remaining"] for r in attacker_rows)
    defender_left = sum(r["remaining"] for r in defender_rows)

    if defender_left == 0 and attacker_left > 0:
        outcome = "attackers take the field"
    elif attacker_left == 0 and defender_left > 0:
        outcome = "defenders hold"
    elif attacker_left == 0 and defender_left == 0:
        outcome = "mutual annihilation"
    else:
        outcome = "both sides survive; the defenders hold"

    return {
        "outcome": outcome,
        "defender_bonus_applied": bool(defender_bonus),
        "attackers": attacker_rows,
        "defenders": defender_rows,
        "attacker_losses": sum(r["lost"] for r in attacker_rows),
        "defender_losses": sum(r["lost"] for r in defender_rows),
        "attackers_remaining": attacker_left,
        "defenders_remaining": defender_left,
        "stalls": stalls,
    }


# ── Costing ───────────────────────────────────────────────────────────────────

def force_cost(
    forces: Sequence[Dict[str, Any]],
    przewalskia: bool = False,
    data: Optional[GameData] = None,
) -> Dict[str, Any]:
    """What raising and equipping a set of forces costs.

    Money and materials are reported separately, and materials are reported twice: as bought
    (crafted parts as parts) and as raw (parts unwound into what they are made of, with the
    crafting fee added). Upkeep is per war tick, which is the only tick that charges it.
    """
    data = data or game_data()

    hire = 0
    gear_money = 0
    materials: Dict[str, int] = {}
    upkeep: Dict[str, int] = {}
    lines = []

    for spec in forces:
        declared = str(spec.get("type", "")).strip()
        unit_row = data.units.get(declared)
        if unit_row is None:
            raise WarcalcError(f"Unknown force type {declared!r}")
        size = max(0, int(spec.get("size", 0)))

        unit_hire = int(unit_row["hire_cost_per_size"]) * size
        hire += unit_hire
        for good, per in (unit_row.get("upkeep_per_size") or {}).items():
            upkeep[good] = upkeep.get(good, 0) + int(per) * size

        line_gear = 0
        for kind, table in (("weapon", data.weapon_cost), ("armor", data.armor_cost)):
            name = spec.get(kind)
            build = table.get(name) if name else None
            if not build:
                continue
            line_gear += int(build.get("money_cost", 0)) * size
            for good, amount in (build.get("consumes") or {}).items():
                materials[good] = materials.get(good, 0) + int(amount) * size
        gear_money += line_gear

        lines.append({
            "name": spec.get("name") or declared,
            "type": declared,
            "size": size,
            "hire": unit_hire,
            "equipment": line_gear,
        })

    raw_materials, craft_money = unwind_to_raw(materials, przewalskia, data)

    return {
        "hire_cost": hire,
        "equipment_cost": gear_money,
        "crafting_cost": craft_money,
        "total_money": hire + gear_money + craft_money,
        "materials_as_bought": materials,
        "materials_as_raw": raw_materials,
        "upkeep_per_war_tick": upkeep,
        "upkeep_note": (
            "Charged only on war ticks (hour 0 and 12 UTC). A force that cannot pay is "
            "deleted outright, not merely weakened."
        ),
        "per_force": lines,
        "przewalskia_pricing": bool(przewalskia),
    }


def unwind_to_raw(
    materials: Dict[str, int],
    przewalskia: bool = False,
    data: Optional[GameData] = None,
) -> Tuple[Dict[str, int], int]:
    """Replace crafted parts with what they are made of, and total the crafting fees.

    Returns ``(raw materials, crafting money)``. One level deep, which is all the game has:
    nothing is crafted from something that is itself crafted.
    """
    data = data or game_data()
    out: Dict[str, int] = {}
    money = 0
    for good, amount in materials.items():
        recipe = data.craft.get(good)
        if recipe is None:
            out[good] = out.get(good, 0) + amount
            continue
        batches = amount / max(1, recipe.get("per", 1))
        cost = recipe.get("money_cost", 0)
        if przewalskia and recipe.get("money_cost_przewalskia") is not None:
            cost = recipe["money_cost_przewalskia"]
        money += int(round(cost * batches))
        for input_good, per in recipe["inputs"].items():
            out[input_good] = out.get(input_good, 0) + int(round(per * batches))
    return out, money
