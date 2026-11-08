"""Shared helpers for the war / CWL / capital / clan games cogs: periods, choices and pure
embed builders (they accept coc.py objects or anything with the same attributes).

Builders follow the house style in ``utils.style``: chips for numbers, bold block headers,
summary lines, and ranking tables in code blocks with the names last."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import discord
from discord import app_commands

from clashhero.repos.war import (
    AttackSummary,
    CWLMemberStats,
    DefenseSummary,
    PlayerWarHistory,
    Standing,
)
from clashhero.utils.fmt import display_width, pad, table, ts
from clashhero.utils.resolve import resolve_player
from clashhero.utils.seasons import game_season, previous_game_season
from clashhero.utils.style import ICONS, SEP, chip, chip_width, header, stars, summary

PERIOD_CHOICES = [
    app_commands.Choice(name="Last 7 days", value="7d"),
    app_commands.Choice(name="Last 30 days", value="30d"),
    app_commands.Choice(name="Last 60 days", value="60d"),
    app_commands.Choice(name="Last 90 days", value="90d"),
    app_commands.Choice(name="This season", value="season"),
    app_commands.Choice(name="Last season", value="last_season"),
    app_commands.Choice(name="All time", value="all"),
]

WAR_TYPE_CHOICES = [
    app_commands.Choice(name="All wars", value="all"),
    app_commands.Choice(name="Regular wars", value="random"),
    app_commands.Choice(name="CWL", value="cwl"),
    app_commands.Choice(name="Friendly wars", value="friendly"),
]

STATE_LABEL = {
    "preparation": "Preparation",
    "inWar": "Battle day",
    "warEnded": "War ended",
    "notInWar": "Not in war",
}

# Discord limits.
FIELD_LIMIT = 1024
DESCRIPTION_LIMIT = 4096
MAX_FIELDS = 25
EMBED_LIMIT = 6000  # all titles, descriptions, fields, footers and authors in one message
BLANK = "\u200b"  # an empty field name


def period_bounds(
    period: str | None, now: datetime | None = None
) -> tuple[datetime | None, datetime | None, str]:
    """(since, until, label) for a PERIOD_CHOICES value. Defaults to 30 days."""
    now = now or datetime.now(UTC)
    period = period or "30d"
    if period.endswith("d") and period[:-1].isdigit():
        days = int(period[:-1])
        return now - timedelta(days=days), None, f"last {days} days"
    if period == "season":
        s = game_season(now)
        return s.start, None, "this season"
    if period == "last_season":
        s = previous_game_season(now)
        return s.start, s.end, "last season"
    return None, None, "all time"


def pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def escape(text: str | None) -> str:
    return discord.utils.escape_markdown(text or "?")


def _th(th: int | None) -> str:
    return str(th) if th else "?"


def state_value(war: Any) -> str:
    st = getattr(war, "state", None)
    return str(getattr(st, "value", st))


async def resolve_player_tags(
    interaction: discord.Interaction, player: str | None, user: discord.abc.User | None
) -> list[str]:
    """One explicit player, else every account linked to ``user`` (or the caller)."""
    if player:
        return [await resolve_player(interaction, player)]
    target = user or interaction.user
    tags = await interaction.client.db.get_linked_tags(target.id)
    if not tags:
        await resolve_player(interaction, None, target)  # raises a friendly error
    return tags


def war_sides(war: Any, clan_tag: str) -> tuple[Any, Any]:
    if getattr(war.clan, "tag", None) == clan_tag:
        return war.clan, war.opponent
    return war.opponent, war.clan


def _when(t: Any) -> datetime | None:
    if t is None:
        return None
    raw = getattr(t, "time", t)
    return raw if raw.tzinfo else raw.replace(tzinfo=UTC)


# ------------------------------------------------------------------ layout helpers


def fit_lines(lines: Iterable[str], limit: int = FIELD_LIMIT) -> str:
    """Join whole lines, stopping before the text would pass ``limit`` characters."""
    out: list[str] = []
    size = 0
    for line in lines:
        cost = len(line) + (1 if out else 0)
        if size + cost > limit:
            if not out:
                out.append(line[:limit])
            break
        out.append(line)
        size += cost
    return "\n".join(out)


def split_block(block: str, limit: int = FIELD_LIMIT) -> list[str]:
    """``block`` in pieces of at most ``limit`` characters, split between lines."""
    pieces: list[str] = []
    cur: list[str] = []
    size = 0
    for line in block.split("\n"):
        line = line[:limit]
        if cur and size + 1 + len(line) > limit:
            pieces.append("\n".join(cur))
            cur, size = [], 0
        size += len(line) + (1 if cur else 0)
        cur.append(line)
    if cur:
        pieces.append("\n".join(cur))
    return pieces


def pack_fields(
    blocks: Sequence[str], *, budget: int = EMBED_LIMIT - 500, max_fields: int = MAX_FIELDS
) -> tuple[list[str], int]:
    """Field values for ``blocks``, one block per field (a block too long for one field continues
    in the next). Stops before passing ``max_fields`` or ``budget`` characters (field names
    included); returns (field values, number of whole blocks that fit)."""
    fields: list[str] = []
    used = 0
    for shown, block in enumerate(blocks):
        parts = split_block(block)
        cost = sum(len(p) + len(BLANK) for p in parts)
        if len(fields) + len(parts) > max_fields or used + cost > budget:
            return fields, shown
        fields += parts
        used += cost
    return fields, len(blocks)


def chip_rows(pairs: Sequence[tuple[object, str]]) -> str:
    """A line per (value, label): the values as chips padded to one width, so they line up."""
    w = chip_width(v for v, _ in pairs)
    return "\n".join(f"{chip(v, w)} {label}" for v, label in pairs)


def rank_table(
    rows: Sequence[Sequence[object]], headers: Sequence[str], *, limit: int = DESCRIPTION_LIMIT - 200
) -> str:
    """Ranking table in a code block: uppercase header, two spaces between columns, values
    right-aligned so digits line up, and the last column (names) unpadded so a long name can't
    push the others out of line. Rows that would take it past ``limit`` characters are left off."""
    cells = [[str(c).replace("`", "'") for c in r] for r in rows]
    last = len(headers) - 1
    widths = [max(display_width(r[i]) for r in [list(headers), *cells]) for i in range(last)]

    def line(r: Sequence[str], *, head: bool = False) -> str:
        cols = [pad(c, widths[i], right=not head) for i, c in enumerate(r[:last])]
        return "  ".join([*cols, r[last]]).rstrip()

    lines = [line(headers, head=True)]
    size = len(lines[0]) + len("```\n\n```")
    for r in cells:
        text = line(r)
        if size + 1 + len(text) > limit:
            break
        lines.append(text)
        size += 1 + len(text)
    return "```\n" + "\n".join(lines) + "\n```"


# ------------------------------------------------------------------ live war


def th_matchup(ours: Sequence[Any], theirs: Sequence[Any]) -> str:
    """A line per Town Hall on either side: a TH chip, then our count and theirs as aligned chips."""

    def count(ms: Sequence[Any]) -> dict[int, int]:
        out: dict[int, int] = {}
        for m in ms:
            out[m.town_hall] = out.get(m.town_hall, 0) + 1
        return out

    a, b = count(ours), count(theirs)
    ths = sorted(set(a) | set(b), reverse=True)
    if not ths:
        return "—"
    lw = chip_width(f"TH{th}" for th in ths)
    nw = chip_width([*a.values(), *b.values()])
    return "\n".join(
        f"{chip(f'TH{th}', lw, right=False)} {chip(a.get(th, 0), nw)} – {chip(b.get(th, 0), nw)}"
        for th in ths
    )


def score_line(our_stars: int, their_stars: int, our_dest: float, their_dest: float) -> str:
    """``⚔️ 42★ – 38★ · 98.2% – 91.0%``"""
    return summary("war", f"{our_stars}★ – {their_stars}★", f"{our_dest:.1f}% – {their_dest:.1f}%")


def attacks_line(ours: int, theirs: int, total: int) -> str:
    """``🗡️ `28/30` – `25/30` attacks``, both chips the same width."""
    a, b = f"{ours}/{total}", f"{theirs}/{total}"
    w = chip_width([a, b])
    return f"🗡️ {chip(a, w)} – {chip(b, w)} attacks"


def war_overview_embed(war: Any, clan_tag: str, color: discord.Color) -> discord.Embed:
    ours, theirs = war_sides(war, clan_tag)
    state = state_value(war)
    is_cwl = bool(getattr(war, "is_cwl", False))
    per = getattr(war, "attacks_per_member", None) or (1 if is_cwl else 2)
    total = war.team_size * per
    if state == "preparation":
        timer = f"battle day starts {ts(_when(war.start_time))}"
    elif state == "inWar":
        timer = f"ends {ts(_when(war.end_time))}"
    else:
        timer = f"ended {ts(_when(war.end_time))}"
    status = [f"**{STATE_LABEL.get(state, state)}**", f"{war.team_size}v{war.team_size}"]
    lines = [SEP.join([*status, *(["CWL"] if is_cwl else []), timer])]
    if state != "preparation":
        lines.append(score_line(ours.stars, theirs.stars, ours.destruction, theirs.destruction))
        lines.append(attacks_line(ours.attacks_used, theirs.attacks_used, total))
    e = discord.Embed(title=f"{ours.name} vs {theirs.name}", description="\n".join(lines), color=color)
    e.add_field(name="Town Halls", value=th_matchup(ours.members, theirs.members))
    return e


def lineup_embed(war: Any, clan_tag: str, color: discord.Color) -> discord.Embed:
    ours, theirs = war_sides(war, clan_tag)
    rows = []
    our_members = sorted(ours.members, key=lambda m: m.map_position)
    their_members = sorted(theirs.members, key=lambda m: m.map_position)
    for i in range(max(len(our_members), len(their_members))):
        a = our_members[i] if i < len(our_members) else None
        b = their_members[i] if i < len(their_members) else None
        rows.append(
            (
                i + 1,
                f"TH{a.town_hall} {a.name[:12]}" if a else "",
                f"TH{b.town_hall} {b.name[:12]}" if b else "",
                _stars_on(war, b) if b else "",
            )
        )
    e = discord.Embed(title=f"Lineup · {ours.name} vs {theirs.name}", color=color)
    e.description = table(rows, ["#", ours.name[:15], theirs.name[:15], "★"])[:4096]
    return e


def _stars_on(war: Any, defender: Any) -> str:
    best = 0
    for a in getattr(war, "attacks", None) or []:
        if a.defender_tag == defender.tag:
            best = max(best, a.stars)
    return stars(best) if best else ""


# ------------------------------------------------------------------ /war history


def history_embed(name: str, label: str, h: PlayerWarHistory, color: discord.Color) -> discord.Embed:
    o, d = h.offense, h.defense
    e = discord.Embed(
        title=f"War history · {name}", description=summary("war", plural(h.wars, "war"), label), color=color
    )
    missed = f" · {o.missed} missed" if o.missed else ""
    e.add_field(
        name=f"{ICONS['war']} Offense",
        value=chip_rows(
            [
                (o.attacks, f"hits{missed}"),
                (pct(o.triple_rate), f"3★ rate ({o.triples})"),
                (f"{o.avg_stars:.2f}", "avg ★"),
                (f"{o.avg_destruction:.1f}%", "avg destruction"),
                (pct(o.fresh_triple_rate), f"fresh-hit 3★ ({o.fresh_triples}/{o.fresh})"),
            ]
        ),
    )
    e.add_field(
        name=f"{ICONS['defense']} Defense",
        value=chip_rows(
            [
                (d.defenses, "times attacked"),
                (d.triples_allowed, "triples allowed"),
                (pct(d.hold_rate), "held"),
                (f"{d.avg_stars:.2f}", "avg ★ allowed"),
            ]
        ),
    )
    e.add_field(name="By TH difference", value=bucket_lines(o), inline=False)
    if h.recent:
        e.add_field(name="Recent attacks", value=recent_attack_lines(h.recent), inline=False)
    return e


def bucket_lines(o: AttackSummary) -> str:
    """Hits and 3★ rate hitting up, even and down, as aligned chips."""
    rows = [(label, *o.bucket_rate(key)) for key, label in (("up", "up"), ("even", "even"), ("down", "down"))]
    lw = chip_width(label for label, _, _ in rows)
    nw = chip_width(n for _, n, _ in rows)
    rw = chip_width(pct(rate) for _, _, rate in rows)
    return "\n".join(
        f"{chip(label, lw, right=False)} {chip(n, nw)} hits · {chip(pct(rate), rw)} 3★"
        for label, n, rate in rows
    )


def recent_attack_lines(recent: Sequence[dict]) -> str:
    """A line per attack: stars, destruction chip, TH matchup, opponent (and CWL), newest first."""
    dests = [f"{a['destruction']:.0f}%" for a in recent]
    w = chip_width(dests)
    lines = [
        f"{stars(a['stars'])} {chip(dest, w)} TH{_th(a['attacker_th'])}→TH{_th(a['defender_th'])}"
        f" · {escape(a['opponent_name'])}{' · CWL' if a['war_type'] == 'cwl' else ''}"
        for a, dest in zip(recent, dests, strict=True)
    ]
    return fit_lines(lines)


# ------------------------------------------------------------------ /stats rankings

RANK_HEADERS = ("#", "TH", "RATE", "HITS", "NAME")


def attack_rank_table(rows: Sequence[tuple[str, AttackSummary]], names: dict[str, str]) -> str:
    """RATE is the 3★ rate, HITS is triples / attacks."""
    data = [
        (i + 1, _th(s.town_hall), f"{s.triple_rate * 100:.1f}", f"{s.triples}/{s.attacks}", names.get(t, t))
        for i, (t, s) in enumerate(rows)
    ]
    return rank_table(data, RANK_HEADERS)


def defense_rank_table(rows: Sequence[tuple[str, DefenseSummary]], names: dict[str, str]) -> str:
    """RATE is the share of defenses held (not tripled), HITS is held / times attacked."""
    data = [
        (
            i + 1,
            _th(s.town_hall),
            f"{s.hold_rate * 100:.1f}",
            f"{s.defenses - s.triples_allowed}/{s.defenses}",
            names.get(t, t),
        )
        for i, (t, s) in enumerate(rows)
    ]
    return rank_table(data, RANK_HEADERS)


def attack_totals(rows: Sequence[tuple[str, AttackSummary]]) -> str:
    """``⚔️ 412 hits · 72.1% 3★ · 2.41★ avg · 7 missed`` across the ranked players."""
    hits = sum(s.attacks for _, s in rows)
    triples = sum(s.triples for _, s in rows)
    star_total = sum(s.stars for _, s in rows)
    missed = sum(s.missed for _, s in rows)
    return summary(
        "war",
        plural(hits, "hit"),
        f"{triples / hits * 100:.1f}% 3★" if hits else None,
        f"{star_total / hits:.2f}★ avg" if hits else None,
        f"{missed} missed" if missed else None,
    )


def defense_totals(rows: Sequence[tuple[str, DefenseSummary]]) -> str:
    """``🛡️ 380 defenses · 81.2% held · 1.62★ avg`` across the ranked players."""
    n = sum(s.defenses for _, s in rows)
    tripled = sum(s.triples_allowed for _, s in rows)
    star_total = sum(s.stars_allowed for _, s in rows)
    return summary(
        "defense",
        plural(n, "defense"),
        f"{(n - tripled) / n * 100:.1f}% held" if n else None,
        f"{star_total / n:.2f}★ avg" if n else None,
    )


def stats_footer(war_type: str | None, period: str, wars: int, town_hall: int | None = None) -> str:
    """``Regular wars · TH16 · last 30 days · 12 wars``"""
    parts = (war_type or "All wars", f"TH{town_hall}" if town_hall else None, period, plural(wars, "war"))
    return SEP.join(p for p in parts if p)


# ------------------------------------------------------------------ CWL season tables


def cwl_season_label(season: str | None) -> str:
    """``2026-11`` -> ``Nov 2026``."""
    try:
        return datetime.strptime(season or "", "%Y-%m").strftime("%b %Y")
    except ValueError:
        return season or "?"


def cwl_member_table(rows: Sequence[CWLMemberStats], *, missed: bool = False) -> str:
    """Season table in the order given (stars, then destruction): total stars, total destruction,
    attacks / rounds in the lineup, Town Hall, name. ``missed`` adds a MISS column."""
    headers = ["#", "★", "DEST", "HIT", "TH", "NAME"]
    if missed:
        headers.insert(4, "MISS")
    data = []
    for i, m in enumerate(rows):
        row: list[object] = [i + 1, m.stars, f"{m.destruction:.0f}%", f"{m.attacks}/{m.rounds}"]
        if missed:
            row.append(m.missed or "")
        data.append([*row, _th(m.town_hall), m.name])
    return rank_table(data, headers)


def standings_table(rows: Sequence[Standing]) -> str:
    data = [
        (i + 1, s.name[:15], s.total_stars, f"{s.destruction:.0f}", f"{s.wins}-{s.losses}-{s.ties}")
        for i, s in enumerate(rows)
    ]
    return table(data, ["#", "Clan", "★", "Dest", "W-L-T"])


# ------------------------------------------------------------------ /cwl history


def cwl_round_line(r: dict, dest_width: int | None = None) -> str:
    """Round chip, stars, destruction chip, enemy base and its TH (``R2 ★★☆ 78% → #21 · TH17``),
    or ``missed`` / not played yet."""
    rnd = chip(f"R{r.get('cwl_round') or '?'}")
    a = r.get("attack")
    if a:
        dest = chip(f"{a['destruction']:.0f}%", dest_width)
        target = f"#{a['target_pos']}" if a.get("target_pos") else "#?"
        th = f"{SEP}TH{a['defender_th']}" if a.get("defender_th") else ""
        return f"{rnd} {stars(a['stars'])} {dest} → {target}{th}"
    state = r.get("state")
    if state == "warEnded":
        return f"{rnd} missed"
    if state == "preparation":
        return f"{rnd} preparation day"
    return f"{rnd} not attacked yet"


def cwl_season_block(rows: Sequence[dict], *, with_name: bool = False) -> str:
    """One account's CWL season from its ``player_cwl_history`` rows (any order): a header with the
    season, the usual map position and Town Hall, the clan, a line per round, then summaries."""
    rows = sorted(rows, key=lambda r: r.get("cwl_round") or 0)
    latest = rows[-1]
    # Most common map position; on a tie, the latest round's (Counter keeps first-seen order).
    positions = Counter(r["map_position"] for r in reversed(rows) if r.get("map_position"))
    pos = positions.most_common(1)[0][0] if positions else None
    th = max((r["town_hall"] for r in rows if r.get("town_hall")), default=None)
    where = SEP.join(p for p in (f"#{pos}" if pos else "", f"TH{th}" if th else "") if p)
    name = f"· {escape(latest.get('name'))}" if with_name else ""
    extra = " ".join(p for p in (name, f"({where})" if where else "") if p)
    lines = [header(cwl_season_label(latest.get("cwl_season")), "cwl", extra or None)]
    lines.append(escape(latest.get("clan_name") or latest.get("clan_tag")))

    attacks = [r["attack"] for r in rows if r.get("attack")]
    w = chip_width(f"{a['destruction']:.0f}%" for a in attacks) or None
    lines += [cwl_round_line(r, w) for r in rows]

    decided = len(attacks) + sum(1 for r in rows if not r.get("attack") and r.get("state") == "warEnded")
    if decided:
        lines.append(
            summary(
                "war",
                f"{len(attacks)}/{decided} wars",
                f"{sum(a['stars'] for a in attacks)}★",
                f"{sum(a['destruction'] for a in attacks):.0f}%",
            )
        )
    defenses = [r["defense"] for r in rows if r.get("defense")]
    if defenses:
        given = sum(d["stars"] for d in defenses)
        lines.append(summary("defense", plural(len(defenses), "defense"), f"{given}★ given"))
    return "\n".join(lines)


def cwl_history_blocks(rows: Sequence[dict]) -> list[str]:
    """A block per CWL season and account, newest season first. With several accounts, each
    header names the account."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (r.get("cwl_season") or "", r.get("player_tag"), r.get("clan_tag"))
        groups.setdefault(key, []).append(r)
    with_name = len({r.get("player_tag") for r in rows}) > 1
    keys = sorted(groups, key=lambda k: k[0], reverse=True)  # stable: keeps the rows' order
    return [cwl_season_block(groups[k], with_name=with_name) for k in keys]


def fill_cwl_history(e: discord.Embed, rows: Sequence[dict]) -> discord.Embed:
    """Add the season blocks to ``e`` as fields, as many as fit in one message, with a footer
    saying how many were left off."""
    blocks = cwl_history_blocks(rows)
    if not blocks:
        e.description = "No CWL wars recorded for these accounts."
        return e
    fields, shown = pack_fields(blocks, budget=EMBED_LIMIT - len(e) - 150)
    for value in fields:
        e.add_field(name=BLANK, value=value, inline=False)
    if shown < len(blocks):
        e.set_footer(text=f"Newest first · showing {shown} of {len(blocks)}; pick a season to see older ones")
    return e
