"""Pure helpers for the info / summary / export / army cogs (no Discord I/O)."""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

from coc import (
    DARK_ELIXIR_TROOP_ORDER,
    ELIXIR_TROOP_ORDER,
    EQUIPMENT,
    HERO_ORDER,
    PETS_ORDER,
    SEASONAL_SPELL_ORDER,
    SEASONAL_TROOP_ORDER,
    SIEGE_MACHINE_ORDER,
    SPELL_ORDER,
    SUPER_TROOP_ORDER,
)
from discord.utils import escape_markdown

from clashhero.services.emojis import EMOJIS
from clashhero.services.snapshots import home_units
from clashhero.utils.fmt import clan_link, pad, role_name, ts
from clashhero.utils.ranked import league_name
from clashhero.utils.style import ICONS, SEP, bar, chip, chip_width, grid, header, summary

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
    return (
        "\n".join(
            f"{EMOJIS.town_hall(th)}`TH{th:>2}` {'▰' * n} **{n}** ({n * 100 // total}%)" for th, n in dist
        )
        or "—"
    )


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


# ------------------------------------------------------------------ player pages
#
# /player info, units, upgrades and rushed are four pages of one card, switched with buttons. The
# builders below return "blocks" (lists of lines: a bold header, rows, an optional summary line);
# pack_blocks() fits them into embed descriptions under Discord's limits.

PLAYER_PAGES = ("profile", "units", "upgrades", "rushed")
PAGE_LABELS = {"profile": "Profile", "units": "Units", "upgrades": "Upgrades", "rushed": "Rushed"}
# Button custom_id: pl:<page>:<tag without #>, plus ":r" on the refresh button (custom_ids must be
# unique within a message, and the current page's own button is also on it).
PLAYER_PAGE_TEMPLATE = (
    r"pl:(?P<page>profile|units|upgrades|rushed):(?P<tag>[0289PYLQGRJCUV]{3,15})(?P<refresh>:r)?"
)
_PLAYER_PAGE_RE = re.compile(PLAYER_PAGE_TEMPLATE)


def player_page_id(page: str, tag: str, *, refresh: bool = False) -> str:
    """custom_id for a player page button: ``pl:units:2PP`` (``pl:units:2PP:r`` to refresh)."""
    if page not in PLAYER_PAGES:
        raise ValueError(f"unknown player page {page!r}")
    return f"pl:{page}:{tag.lstrip('#').upper()}" + (":r" if refresh else "")


def parse_player_page_id(custom_id: str) -> tuple[str, str, bool] | None:
    """(page, "#TAG", refresh) from a player page button's custom_id, or None."""
    m = _PLAYER_PAGE_RE.fullmatch(custom_id)
    return (m["page"], f"#{m['tag']}", bool(m["refresh"])) if m else None


DESC_LIMIT = 4096  # embed description
FIELD_LIMIT = 1024  # embed field value
# Discord caps a message's embeds at 6000 characters in all; leave room for titles, footers and the
# maintenance notice.
MESSAGE_BUDGET = 5600
GRID_COLUMNS = 52  # monospace columns a row of chips may take (fits a desktop embed)
MORE_NOT_SHOWN = "-# … more not shown (too long for one Discord message)"

UNIT_GROUPS = ("troops", "dark", "spells", "heroes", "pets", "equipment", "sieges")
GROUP_TITLES = {
    "troops": "Troops",
    "dark": "Dark Troops",
    "spells": "Spells",
    "heroes": "Heroes",
    "pets": "Pets",
    "equipment": "Hero Equipment",
    "sieges": "Siege Machines",
}
LAB_CATEGORIES = ("troops", "spells", "sieges")
_DARK_TROOPS = frozenset(DARK_ELIXIR_TROOP_ORDER)
_GAME_ORDER = {
    name: i
    for i, name in enumerate(
        [
            *ELIXIR_TROOP_ORDER,
            *DARK_ELIXIR_TROOP_ORDER,
            *SPELL_ORDER,
            *HERO_ORDER,
            *PETS_ORDER,
            *EQUIPMENT,
            *SIEGE_MACHINE_ORDER,
        ]
    )
}
APPROX_NOTE = "`~` this Town Hall's max isn't known yet, so the game's overall max is shown"
EQUIPMENT_NOTE = "-# Equipment isn't counted: it's limited by ore, not lab or builder time."


def unit_group(u: UnitLevel) -> str:
    """Page section for a unit: its category, with dark elixir troops split from troops."""
    return "dark" if u.category == "troops" and u.name in _DARK_TROOPS else u.category


def group_units(units: Iterable[UnitLevel]) -> list[tuple[str, list[UnitLevel]]]:
    """[(group, units)] in page order, units in the game's order (units it doesn't know last)."""
    groups: dict[str, list[UnitLevel]] = {}
    for u in units:
        groups.setdefault(unit_group(u), []).append(u)
    order = [g for g in UNIT_GROUPS if g in groups] + [g for g in groups if g not in UNIT_GROUPS]
    last = len(_GAME_ORDER)
    return [(g, sorted(groups[g], key=lambda u: _GAME_ORDER.get(u.name, last))) for g in order]


def group_header(group: str, extra: str | None = None) -> str:
    return header(GROUP_TITLES.get(group, group.title()), group if group in ICONS else "▫️", extra)


def unit_label(u: UnitLevel) -> str:
    """Display name; spells drop the " Spell" suffix (the section header already says Spells)."""
    return u.name.removesuffix(" Spell") if u.category == "spells" else u.name


def level_text(level: int, max_level: int | None, approximate: bool = False) -> str:
    """``10/14``; ``10/~14`` when the max is the game's overall max rather than the TH's."""
    if max_level is None:
        return f"{level}/?"
    return f"{level}/{'~' if approximate and level < max_level else ''}{max_level}"


def is_maxed(u: UnitLevel) -> bool:
    return u.max_for_th is not None and u.level >= u.max_for_th


def counts_as_rushed(u: UnitLevel) -> bool:
    """Below the previous TH's max, in a category the rushed check counts (not equipment)."""
    return u.is_rushed and u.category in RUSH_CATEGORIES


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def percent(part: int, total: int, *, digits: int = 1, round_up: bool = False, empty: float = 0.0) -> str:
    """``part/total`` as ``82.4%``. Rounds down by default, so unfinished work never shows 100%;
    ``round_up`` for rushed %, so a small gap never shows 0%. ``empty`` is used when total is 0."""
    if total <= 0:
        value = empty
    else:
        scale = 10**digits
        n = 100 * scale * part
        value = (-(-n // total) if round_up else n // total) / scale
    return f"{value:.{digits}f}%"


def progress_bar(done: int, total: int, width: int = 12) -> str:
    """``▰▰▰▱`` rounded down, so the bar is only full when everything is done."""
    filled = width if total <= 0 else width * min(done, total) // total
    return bar(filled / width, width)


def chip_grid(
    cells: Sequence[tuple[str, str, str]], *, columns: int = GRID_COLUMNS, max_per_row: int = 3
) -> list[str]:
    """Rows of ``Name  10/14✓`` chips. Cells are (name, level text, mark). Takes as many chips
    per row as fit in ``columns`` monospace columns, and pads each column's chips to one width so
    the columns line up."""
    if not cells:
        return []
    icons = [unit_icon(name) for name, _, _ in cells]
    if all(icons):  # every unit has an uploaded icon: icon + level chip, more per row
        lw = chip_width(f"{lv}{mark}" for _, lv, mark in cells)
        chips = [f"{icon}{chip(f'{lv}{mark}', lw)}" for icon, (_, lv, mark) in zip(icons, cells, strict=True)]
        return grid(chips, 4)
    for per_row in range(max(1, min(max_per_row, len(cells))), 0, -1):
        widths = [
            tuple(chip_width(cell[k] for cell in cells[c::per_row]) for k in range(3)) for c in range(per_row)
        ]
        if per_row == 1 or sum(n + 1 + lv + m for n, lv, m in widths) + per_row - 1 <= columns:
            break
    chips = []
    for i, (name, lv, mark) in enumerate(cells):
        nw, lw, mw = widths[i % per_row]
        chips.append(chip(f"{pad(name, nw)} {pad(lv, lw, right=True)}{pad(mark, mw)}"))
    return grid(chips, per_row)


def pack_blocks(
    blocks: Iterable[Sequence[str]],
    *,
    limit: int = DESC_LIMIT,
    budget: int = MESSAGE_BUDGET,
    max_pages: int = 10,
) -> list[str]:
    """Join blocks into embed descriptions (blank line between blocks), each under ``limit``
    characters and all together under ``budget``. A block is only split (between lines) when it
    is longer than ``limit`` by itself. What doesn't fit is dropped with a short note."""
    pieces: list[str] = []
    for block in blocks:
        cur = ""
        for line in (ln[:limit] for ln in block if ln):
            if cur and len(cur) + 1 + len(line) > limit:
                pieces.append(cur)
                cur = line
            else:
                cur = f"{cur}\n{line}" if cur else line
        if cur:
            pieces.append(cur)
    pages: list[str] = []
    used, reserve = 0, len(MORE_NOT_SHOWN) + 2
    for i, piece in enumerate(pieces):
        joins = bool(pages) and len(pages[-1]) + 2 + len(piece) <= limit
        cost = len(piece) + (2 if joins else 0)
        room = budget - used - (0 if i == len(pieces) - 1 else reserve)
        if cost > room or (not joins and len(pages) >= max_pages):
            if pages and len(pages[-1]) + reserve <= limit:
                pages[-1] += "\n\n" + MORE_NOT_SHOWN
            elif len(pages) < max_pages:
                pages.append(MORE_NOT_SHOWN)
            break
        if joins:
            pages[-1] += "\n\n" + piece
        else:
            pages.append(piece)
        used += cost
    return pages or [""]


def units_blocks(units: Sequence[UnitLevel], town_hall: int) -> list[list[str]]:
    """/player units: every unit's level against its max at the player's Town Hall."""
    if not units:
        return [["No units found for this player."]]
    _, prog = upgrade_progress(units)
    maxed = sum(is_maxed(u) for u in units)
    blocks = [
        [
            header("Units", "📊", f"at TH{town_hall}"),
            f"{progress_bar(prog.done, prog.total)} **{percent(prog.done, prog.total, empty=100)}**"
            f" of max levels{SEP}{maxed}/{len(units)} maxed",
        ]
    ]
    for group, members in group_units(units):
        cells = [
            (unit_label(u), level_text(u.level, u.max_for_th, u.approximate), "✓" if is_maxed(u) else "")
            for u in members
        ]
        done = sum(is_maxed(u) for u in members)
        blocks.append([group_header(group, f"· {done}/{len(members)} maxed"), *chip_grid(cells)])
    if any(u.approximate and not is_maxed(u) for u in units):
        blocks.append([f"-# {APPROX_NOTE}"])
    return blocks


def upgrades_blocks(units: Sequence[UnitLevel], town_hall: int) -> list[list[str]]:
    """/player upgrades: units below their max at the player's Town Hall, per section."""
    pending, prog = upgrade_progress(units)
    if not pending:
        return [[header("Upgrades", "⬆️", f"at TH{town_hall}"), f"Everything is maxed for TH{town_hall}. 🎉"]]
    levels_left = sum(u.remaining for u in pending)
    blocks = [
        [
            header("Upgrades left", "⬆️", f"at TH{town_hall}"),
            f"{plural(len(pending), 'upgrade')}{SEP}{plural(levels_left, 'level')}",
        ]
    ]
    for group, members in group_units(units):
        todo = [u for u in members if u.remaining]
        if not todo:
            continue
        _, gp = upgrade_progress(members)
        names = [unit_label(u) for u in todo]
        levels = [level_text(u.level, u.max_for_th, u.approximate) for u in todo]
        nw, lw = chip_width(names), chip_width(levels)
        rows = [
            chip(f"{pad(name, nw)} {pad(lv, lw, right=True)}")
            + (f" {chip('R')}" if counts_as_rushed(u) else "")
            for name, lv, u in zip(names, levels, todo, strict=True)
        ]
        blocks.append(
            [
                group_header(group),
                *rows,
                summary(
                    "time",
                    plural(len(todo), "upgrade"),
                    plural(sum(u.remaining for u in todo), "level"),
                    f"{percent(gp.done, gp.total, digits=0, empty=100)} done",
                ),
            ]
        )
    blocks.append(
        [
            f"{progress_bar(prog.done, prog.total)} **{percent(prog.done, prog.total, empty=100)}**"
            f" of TH{town_hall} max levels done"
        ]
    )
    notes = []
    if any(counts_as_rushed(u) for u in pending):
        notes.append(f"`R` rushed: below the TH{town_hall - 1} max")
    if any(u.approximate for u in pending):
        notes.append(APPROX_NOTE)
    if notes:
        blocks.append([f"-# {SEP.join(notes)}"])
    return blocks


@dataclass(frozen=True)
class RushReport:
    lab: Progress  # done = levels missing below the previous TH's max
    heroes: Progress
    overall: Progress
    rushed: list[UnitLevel]
    skipped: int  # units whose previous-TH max isn't known (coc.py's data is behind the game)


def rush_report(units: Iterable[UnitLevel]) -> RushReport:
    """Rushed % for the lab (troops, spells, siege machines), heroes and overall (also pets).
    Equipment isn't counted (see rushed_progress)."""
    units = list(units)
    rushed, overall = rushed_progress(units)
    _, lab = rushed_progress(u for u in units if u.category in LAB_CATEGORIES)
    _, heroes = rushed_progress(u for u in units if u.category == "heroes")
    skipped = sum(1 for u in units if u.category in RUSH_CATEGORIES and u.approximate)
    return RushReport(lab, heroes, overall, rushed, skipped)


def rushed_blocks(units: Sequence[UnitLevel], town_hall: int) -> list[list[str]]:
    """/player rushed: units below the previous Town Hall's max, with rushed percentages."""
    r = rush_report(units)
    prev = town_hall - 1
    if r.rushed:
        top = [
            header("Rushed", "rushed", f"vs TH{prev} max"),
            SEP.join(
                f"{icon} {name} {chip(percent(p.done, p.total, round_up=True))}"
                for icon, name, p in (
                    ("🧪", "Lab", r.lab),
                    (ICONS["heroes"], "Heroes", r.heroes),
                    ("📊", "Overall", r.overall),
                )
            ),
        ]
    else:
        top = [header("Not rushed", "done", "🎉")]
        if prev >= 1:
            top.append(f"Every unit is at least TH{prev} max.")
    top.append(EQUIPMENT_NOTE)
    if r.skipped:
        top.append(f"-# {plural(r.skipped, 'unit')} skipped (TH{prev} max not known yet).")
    blocks = [top]
    for group, members in group_units(r.rushed):
        cells = [(unit_label(u), f"{u.level}/{u.max_for_prev_th}", "") for u in members]
        blocks.append([group_header(group, f"· {len(members)} rushed"), *chip_grid(cells)])
    return blocks


def fit(text: str, limit: int = FIELD_LIMIT, sep: str = SEP) -> str:
    """Cut a ``sep``-joined list to ``limit`` characters at an item boundary, ending with ``…``."""
    if len(text) <= limit:
        return text
    cut = text.rfind(sep, 0, limit - 2)
    return (text[:cut] if cut > 0 else text[: limit - 2]) + " …"


def unit_icon(name: str) -> str:
    """Uploaded emoji for a unit (spells may be listed without their " Spell" suffix)."""
    return EMOJIS.unit(name) or EMOJIS.unit(f"{name} Spell")


def unit_chips(units: Iterable[tuple[str, int]]) -> str:
    """``Barbarian King `95` · Archer Queen `95``` for the profile page (icon instead of the name
    when one is uploaded)."""
    return fit(SEP.join(f"{unit_icon(name) or escape_markdown(name)} {chip(level)}" for name, level in units))


def profile_parts(
    p: Any,
    *,
    discord_id: int | None = None,
    verified: bool = False,
    last_seen: str | datetime | None = None,
    season: tuple[int, int] | None = None,
    names: Sequence[str] = (),
) -> tuple[str, list[tuple[str, str, bool]]]:
    """(description, [(field name, value, inline)]) for /player info.

    ``p`` is a coc.py Player; the rest comes from the database (linked Discord user, last seen,
    season donations given/received, earlier names)."""
    weapon = getattr(p, "town_hall_weapon", None)
    th = [
        header(f"Town Hall {p.town_hall}", EMOJIS.town_hall(p.town_hall) or "🏠"),
        *([f"weapon {chip(weapon)}"] if weapon else []),
    ]
    lines = [SEP.join([*th, f"level {chip(p.exp_level)}"])]
    clan = getattr(p, "clan", None)
    if clan:
        role = role_name(_enum(getattr(p, "role", None)) or None)
        lines.append(f"{ICONS['clan']} [{escape_markdown(clan.name)}]({clan_link(clan.tag)}){SEP}{role}")
    else:
        lines.append(f"{ICONS['clan']} Not in a clan")

    fields: list[tuple[str, str, bool]] = []
    league = league_name(p) or "Unranked"
    fields.append(
        (
            f"{ICONS['trophy']} Trophies",
            f"{chip(f'{p.trophies:,}')} best {chip(f'{p.best_trophies:,}')}\n"
            f"{EMOJIS.league(league)} {league}".rstrip(),
            True,
        )
    )
    pref = getattr(p, "war_opted_in", None)
    pref_text = "Opted in" if pref else "Opted out" if pref is False else "Preference unknown"
    fields.append((f"{ICONS['war']} War", f"{chip(f'{p.war_stars:,}★')}\n{pref_text}", True))
    gold = getattr(p, "clan_capital_contributions", 0) or 0
    fields.append((f"{ICONS['capital']} Capital", f"{chip(f'{gold:,}')} gold", True))
    if season is not None:
        given, received = season
        fields.append(("🎁 Donations", f"{chip(f'{given:,}')} given\n{chip(f'{received:,}')} received", True))
    if p.attack_wins or p.defense_wins:  # the API reports 0/0 for everyone since the ranked rework
        fields.append(("🗡️ Wins", f"{chip(p.attack_wins)} attacks\n{chip(p.defense_wins)} defenses", True))
    if discord_id:
        fields.append(("🔗 Discord", f"<@{discord_id}>" + (" ✅" if verified else ""), True))
    if last_seen:
        fields.append(("👀 Last seen", ts(last_seen), True))
    for title, kind in (("Heroes", "heroes"), ("Equipment", "equipment"), ("Pets", "pets")):
        units = home_units(p, kind)
        if units:
            fields.append((f"{ICONS[kind]} {title}", unit_chips(units), False))
    if names:
        fields.append(("📝 Previous names", fit(SEP.join(escape_markdown(n) for n in names[-10:])), False))
    return "\n".join(lines), fields


def _enum(v: Any) -> str:
    return str(getattr(v, "value", v)) if v is not None else ""


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
