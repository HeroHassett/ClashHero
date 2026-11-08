"""Pure helpers for the info / summary / export / army cogs (no Discord I/O)."""

from __future__ import annotations

import csv
import io
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

from coc import (
    PETS_ORDER,
    SEASONAL_SPELL_ORDER,
    SEASONAL_TROOP_ORDER,
    SIEGE_MACHINE_ORDER,
    SUPER_TROOP_ORDER,
)

# ------------------------------------------------------------------ time


def parse_iso(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def ago(value: str | datetime | None, now: datetime) -> str:
    """Compact relative age for monospace tables: '5m', '3h', '2d', '—'."""
    dt = parse_iso(value)
    if dt is None:
        return "—"
    secs = max(0, int((now - dt).total_seconds()))
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def short(name: str | None, width: int = 15) -> str:
    name = name or "?"
    return name if len(name) <= width else name[: width - 1] + "…"


# ------------------------------------------------------------------ activity


def activity_histogram(rows: Iterable[tuple[str, int]], offset_minutes: int = 0) -> list[int]:
    """Sum activity into 24 local-hour buckets. ``rows`` are ("YYYY-MM-DDTHH" UTC, count)."""
    buckets = [0] * 24
    shift = timedelta(minutes=offset_minutes)
    for hour, count in rows:
        dt = datetime.strptime(hour, "%Y-%m-%dT%H") + shift
        buckets[dt.hour] += count
    return buckets


def render_histogram(buckets: Sequence[int], width: int = 20) -> str:
    top = max(buckets) if buckets else 0
    lines = []
    for h, n in enumerate(buckets):
        bar = "█" * (round(n / top * width) if top else 0)
        lines.append(f"{h:02d}:00 {bar} {n}")
    return "```\n" + "\n".join(lines) + "\n```"


# ------------------------------------------------------------------ town hall composition


def th_distribution(town_halls: Iterable[int | None]) -> list[tuple[int, int]]:
    """[(th, count)] highest TH first."""
    c = Counter(th for th in town_halls if th)
    return sorted(c.items(), reverse=True)


def render_th_distribution(dist: Sequence[tuple[int, int]]) -> str:
    total = sum(n for _, n in dist) or 1
    return "\n".join(f"`TH{th:>2}` {'▰' * n} **{n}** ({n * 100 // total}%)" for th, n in dist) or "—"


# ------------------------------------------------------------------ unit progress


@dataclass(frozen=True)
class UnitLevel:
    name: str
    category: str  # troops | spells | sieges | heroes | pets | equipment
    level: int
    max_for_th: int | None  # max level at the player's TH
    max_for_prev_th: int | None  # max level at TH - 1 (None if locked there)
    approximate: bool = False  # coc.py's data is behind the game; max_for_th is the overall max

    @property
    def remaining(self) -> int:
        return max(0, (self.max_for_th or 0) - self.level)

    @property
    def is_rushed(self) -> bool:
        return bool(self.max_for_prev_th) and self.level < (self.max_for_prev_th or 0)


@dataclass(frozen=True)
class Progress:
    done: int
    total: int

    @property
    def percent(self) -> float:
        return 100.0 * self.done / self.total if self.total else 100.0


RUSH_CATEGORIES = ("troops", "spells", "sieges", "heroes", "pets")


def rushed_progress(units: Iterable[UnitLevel]) -> tuple[list[UnitLevel], Progress]:
    """Units below their previous-TH max, and the % of levels missing vs previous-TH max.

    Rushed % = levels missing below previous-TH max / sum of previous-TH max levels.
    """
    rushed, missing, total = [], 0, 0
    for u in units:
        if u.category not in RUSH_CATEGORIES or not u.max_for_prev_th:
            continue
        total += u.max_for_prev_th
        gap = max(0, u.max_for_prev_th - u.level)
        missing += gap
        if gap:
            rushed.append(u)
    return rushed, Progress(missing, total)


def upgrade_progress(units: Iterable[UnitLevel]) -> tuple[list[UnitLevel], Progress]:
    """Units not maxed for the current TH, and overall % of levels completed."""
    pending, done, total = [], 0, 0
    for u in units:
        if not u.max_for_th:
            continue
        total += u.max_for_th
        done += min(u.level, u.max_for_th)
        if u.remaining:
            pending.append(u)
    return pending, Progress(done, total)


def _max_for(unit: Any, th: int) -> int | None:
    if unit is None or th <= 0:
        return None
    try:
        return unit.get_max_level_for_townhall(th)
    except Exception:
        return None


def unit_levels(player: Any) -> list[UnitLevel]:
    """Home-village unit levels for a coc.py Player.

    Names and levels come from the raw API payload, which is always current. Per-Town-Hall max
    levels come from coc.py's bundled game data, looked up per unit; when that data is missing or
    out of date (the player is already past what it lists), the max is None ("unknown") rather
    than an error. coc.py's own Player.troops can raise on levels it doesn't know, so it's only
    used as a fallback when no raw payload is available.
    """
    th = player.town_hall or 0
    raw = getattr(player, "_raw_data", None)
    if not isinstance(raw, dict):
        return _unit_levels_from_models(player, th)
    client = getattr(player, "_client", None)
    skip = set(SUPER_TROOP_ORDER) | set(SEASONAL_TROOP_ORDER) | set(SEASONAL_SPELL_ORDER)
    out: list[UnitLevel] = []
    for key, default_cat in (
        ("troops", "troops"),
        ("spells", "spells"),
        ("heroes", "heroes"),
        ("heroEquipment", "equipment"),
    ):
        for u in raw.get(key) or []:
            name, level = u.get("name"), int(u.get("level") or 0)
            if not name or u.get("village", "home") != "home" or name in skip:
                continue
            cat = "pets" if name in PETS_ORDER else "sieges" if name in SIEGE_MACHINE_ORDER else default_cat
            static = _static_unit(client, cat, name)
            max_th, max_prev = _max_for(static, th), _max_for(static, th - 1)
            approximate = False
            if max_th is None or level > max_th:
                # coc.py doesn't know this unit or its newest levels (it lags game updates): use
                # the game's overall max from the API, and leave the previous-TH max unknown so
                # /player rushed doesn't guess.
                max_th, max_prev, approximate = u.get("maxLevel"), None, True
            out.append(UnitLevel(name, cat, level, max_th, max_prev, approximate))
    return out


_STATIC_GETTERS = {
    "troops": lambda c, n: c.get_troop(n, True, 1),
    "sieges": lambda c, n: c.get_troop(n, True, 1),
    "spells": lambda c, n: c.get_spell(n, 1),
    "heroes": lambda c, n: c.get_hero(n, 1),
    "pets": lambda c, n: c.get_pet(n, 1),
    "equipment": lambda c, n: c.get_equipment(n, 1),
}


def _static_unit(client: Any, category: str, name: str) -> Any:
    if client is None:
        return None
    try:
        return _STATIC_GETTERS[category](client, name)
    except Exception:
        return None


def _unit_levels_from_models(player: Any, th: int) -> list[UnitLevel]:
    out: list[UnitLevel] = []

    def add(attr: str, category: str) -> None:
        try:
            units = getattr(player, attr, None) or []
        except Exception:  # coc.py can't parse a level it doesn't know
            return
        for u in units:
            if getattr(u, "is_super_troop", False) or getattr(u, "is_seasonal", False):
                continue
            if hasattr(u, "is_home_base") and not u.is_home_base:
                continue
            cat = "sieges" if getattr(u, "is_siege_machine", False) else category
            out.append(UnitLevel(u.name, cat, u.level, _max_for(u, th), _max_for(u, th - 1)))

    add("home_troops", "troops")
    add("spells", "spells")
    add("heroes", "heroes")
    add("pets", "pets")
    add("equipment", "equipment")
    return out


def format_units(units: Sequence[UnitLevel], *, show: str = "level") -> dict[str, list[str]]:
    """Group into category -> lines. show='level' => 'Name 5/7'; 'remaining' => 'Name 5→7'."""
    out: dict[str, list[str]] = {}
    for u in units:
        mx = u.max_for_th or u.level
        if show == "remaining":
            line = f"{u.name} {u.level}→{mx}"
        elif show == "rushed":
            line = f"{u.name} {u.level}/{u.max_for_prev_th}"
        else:
            line = f"{u.name} {u.level}/{mx}" + (" ✅" if u.level >= mx else "")
        out.setdefault(u.category, []).append(line)
    return out


# ------------------------------------------------------------------ CSV


def to_csv(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return buf.getvalue().encode("utf-8-sig")  # BOM so Excel detects UTF-8 (player names)


def rows_from(records: Iterable[Any], columns: Sequence[str]) -> list[list[Any]]:
    """Pick ``columns`` out of sqlite Rows / dicts."""
    return [[r[c] for c in columns] for r in records]


# ------------------------------------------------------------------ armies


def army_param(link: str) -> str:
    """Extract the ``army`` value from a share link, or return the input if it's already raw."""
    link = link.strip()
    if "army=" not in link:
        if link and link[0] in "hidus" and any(ch.isdigit() for ch in link):
            return link
        raise ValueError("That doesn't look like an army link.")
    q = parse_qs(urlparse(link).query)
    if "army" in q:
        return q["army"][0]
    return link.split("army=", 1)[1].split("&", 1)[0]


def army_link(param: str) -> str:
    return f"https://link.clashofclans.com/en?action=CopyArmy&army={param}"


@dataclass
class ArmySummary:
    troops: list[tuple[str, int, int]]  # (name, count, housing each)
    spells: list[tuple[str, int, int]]
    cc_troops: list[tuple[str, int]]
    cc_spells: list[tuple[str, int]]
    heroes: list[tuple[str, str | None, list[str]]]  # (hero, pet, equipment)

    @property
    def troop_space(self) -> int:
        return sum(n * hs for _, n, hs in self.troops)

    @property
    def spell_space(self) -> int:
        return sum(n * hs for _, n, hs in self.spells)


def _housing(unit: Any) -> int:
    try:
        return int(unit.housing_space or 0)
    except Exception:
        return 0


def summarize_army(recipe: Any) -> ArmySummary:
    heroes = []
    for h in recipe.heroes_loadout or []:
        hero = getattr(getattr(h, "hero", None), "name", None) or "?"
        pet = getattr(getattr(h, "pet", None), "name", None)
        eq = [getattr(e, "name", "?") for e in (getattr(h, "equipment", None) or []) if e]
        heroes.append((hero, pet, eq))
    return ArmySummary(
        troops=[(t.name, n, _housing(t)) for t, n in recipe.troops],
        spells=[(s.name, n, _housing(s)) for s, n in recipe.spells],
        cc_troops=[(t.name, n) for t, n in recipe.clan_castle_troops],
        cc_spells=[(s.name, n) for s, n in recipe.clan_castle_spells],
        heroes=heroes,
    )
