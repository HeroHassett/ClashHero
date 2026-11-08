"""Pure embed builders for legend features (no I/O; unit-tested)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import discord

from clashhero.repos.legends import BoardEntry, DayStats, SeasonSummary, WeekDay
from clashhero.utils.fmt import player_link, signed, table, ts

ATTACK_COLOR = discord.Color.green()
DEFENSE_COLOR = discord.Color.red()


def _hits(values: Sequence[int]) -> str:
    return " ".join(signed(v) for v in values) or "—"


def count(hits: Sequence[int], cap: int | None) -> str:
    """ "3/8" for tiers with a daily cap (Legend I), plain "3" otherwise."""
    return f"{len(hits)}/{cap}" if cap else str(len(hits))


def week_embed(
    *,
    name: str,
    tag: str,
    league: str | None,
    week_id: str,
    days: Sequence[WeekDay],
    weekly_cap: int | None,
    color: discord.Color | None = None,
) -> discord.Embed:
    """A ranked week (Monday 05:00 UTC to Monday): one row per legend day plus totals."""
    rows, att, dfn, net = [], 0, 0, 0
    for d in days:
        s = d.stats
        att += len(s.attacks)
        dfn += len(s.defenses)
        net += s.net
        rows.append(
            [
                d.day[5:],
                d.start if d.start is not None else "-",
                d.end if d.end is not None else "-",
                f"{len(s.attacks)} ({signed(s.offense)})",
                f"{len(s.defenses)} ({signed(s.defense)})",
                signed(s.net),
            ]
        )
    e = discord.Embed(title=f"{name} · Ranked week of {week_id}", url=player_link(tag), color=color)
    e.description = table(rows, ["Day", "Start", "End", "Att", "Def", "Net"])
    e.add_field(name="Attacks", value=f"{att}/{weekly_cap}" if weekly_cap else str(att))
    e.add_field(name="Defenses", value=str(dfn))
    e.add_field(name="Net", value=signed(net))
    if league:
        e.add_field(name="League", value=league)
    e.set_footer(text=f"{tag} · battles are inferred from trophy changes · days reset 05:00 UTC")
    return e


def alert_embed(
    *,
    name: str,
    tag: str,
    kind: str,
    trophies: int,
    trophies_after: int,
    stats: DayStats,
    occurred_at: datetime | None = None,
) -> discord.Embed:
    is_attack = kind == "attack"
    e = discord.Embed(
        title=f"{'⚔️ Attack' if is_attack else '🛡️ Defense'} · {name}",
        url=player_link(tag),
        color=ATTACK_COLOR if is_attack else DEFENSE_COLOR,
        description=f"**{signed(trophies)}** trophies → **{trophies_after}**",
    )
    e.add_field(name="Attacks", value=f"{count(stats.attacks, stats.cap)} ({signed(stats.offense)})")
    e.add_field(name="Defenses", value=f"{count(stats.defenses, stats.cap)} ({signed(stats.defense)})")
    e.add_field(name="Net today", value=signed(stats.net))
    e.set_footer(text=tag)
    if occurred_at:
        e.timestamp = occurred_at
    return e


def day_embed(
    *,
    name: str,
    tag: str,
    day: str,
    stats: DayStats,
    start: int | None,
    end: int | None,
    color: discord.Color | None = None,
) -> discord.Embed:
    e = discord.Embed(title=f"{name} · Legend day {day}", url=player_link(tag), color=color)
    if start is not None:
        e.description = f"Trophies **{start}** → **{end if end is not None else start}**"
    e.add_field(
        name=f"Attacks {count(stats.attacks, stats.cap)} ({signed(stats.offense)})",
        value=_hits(stats.attacks),
        inline=False,
    )
    e.add_field(
        name=f"Defenses {count(stats.defenses, stats.cap)} ({signed(stats.defense)})",
        value=_hits(stats.defenses),
        inline=False,
    )
    e.add_field(name="Net", value=signed(stats.net))
    if stats.attacks_left is not None:
        e.add_field(name="Attacks left", value=str(stats.attacks_left))
    e.set_footer(text=f"{tag} · battles are inferred from trophy changes")
    return e


def season_rows(summary: SeasonSummary) -> list[list[Any]]:
    rows = []
    for d in summary.days:
        s = d.stats
        rows.append(
            [
                d.day[5:],
                d.start if d.start is not None else "?",
                d.end if d.end is not None else "?",
                signed(s.offense),
                signed(s.defense),
                f"{len(s.attacks)}/{len(s.defenses)}",
            ]
        )
    return rows


def season_embed(
    *, name: str, tag: str, season_id: str, summary: SeasonSummary, color: discord.Color | None = None
) -> discord.Embed:
    e = discord.Embed(title=f"{name} · Season ending {season_id}", url=player_link(tag), color=color)
    if not summary.days:
        e.description = "No legend data recorded for this season."
        return e
    e.description = table(season_rows(summary), ["Day", "Start", "End", "Off", "Def", "A/D"])[:4000]
    a, d = summary.all_attacks, summary.all_defenses
    e.add_field(name="Attacks", value=f"{len(a)} · avg {summary.avg_offense:+.1f}")
    e.add_field(name="Defenses", value=f"{len(d)} · avg {summary.avg_defense:+.1f}")
    e.add_field(name="Max-trophy hits", value=f"{summary.triple_rate:.0%}")
    e.add_field(name="Avg lost on defense / day", value=f"{summary.avg_defense_per_day:+.1f}")
    e.set_footer(text=tag)
    return e


def history_embed(
    *,
    name: str,
    tag: str,
    seasons: Sequence[Any],
    best_trophies: int | None,
    names: Sequence[str],
    color: discord.Color | None = None,
) -> discord.Embed:
    e = discord.Embed(title=f"{name} · Legend history", url=player_link(tag), color=color)
    if seasons:
        rows = [
            [s["season_id"], s["final_trophies"] or "?", s["peak_trophies"] or "?", s["days"], s["attacks"]]
            for s in seasons
        ]
        e.description = table(rows, ["Season", "Final", "Peak", "Days", "Hits"])[:4000]
    else:
        e.description = "No seasons recorded yet."
    if best_trophies:
        e.add_field(name="All-time best", value=str(best_trophies))
    if len(names) > 1:
        e.add_field(name="Name history", value=" → ".join(names)[:1024], inline=False)
    e.set_footer(text=tag)
    return e


def board_lines(entries: Sequence[BoardEntry]) -> list[list[Any]]:
    return [
        [
            i,
            e.name[:15],
            e.town_hall or "?",
            e.trophies,
            signed(e.stats.net),
            count(e.stats.attacks, e.stats.cap),
            count(e.stats.defenses, e.stats.cap),
        ]
        for i, e in enumerate(entries, 1)
    ]


def board_embed(
    *,
    title: str,
    entries: Sequence[BoardEntry],
    updated: datetime | None = None,
    color: discord.Color | None = None,
) -> discord.Embed:
    e = discord.Embed(title=title, color=color)
    if entries:
        e.description = table(board_lines(entries), ["#", "Name", "TH", "Cups", "Today", "Atk", "Def"])[:4000]
    else:
        e.description = "No players yet."
    if updated:
        e.add_field(name="Updated", value=ts(updated))
    return e


def clan_day_embed(
    *, clan_name: str, clan_tag: str, day: str, rows: Sequence[dict], color: discord.Color | None = None
) -> discord.Embed:
    e = discord.Embed(title=f"{clan_name} · Legend day {day} summary", color=color)
    if not rows:
        e.description = "No legend activity recorded."
        return e
    lines = [
        [
            i,
            r["name"][:15],
            r["start"],
            r["end"],
            signed(r["offense"]),
            signed(r["defense"]),
            signed(r["net"]),
        ]
        for i, r in enumerate(rows, 1)
    ]
    e.description = table(lines, ["#", "Name", "Start", "End", "Off", "Def", "Net"])[:4000]
    tot_off = sum(r["offense"] for r in rows)
    tot_def = sum(r["defense"] for r in rows)
    e.add_field(name="Clan offense", value=signed(tot_off))
    e.add_field(name="Clan defense", value=signed(tot_def))
    e.set_footer(text=clan_tag)
    return e


def top_players_embed(
    *,
    title: str,
    players: Sequence[Any],
    page: int,
    pages: int,
    highlight: set[str],
    color: discord.Color | None = None,
) -> discord.Embed:
    rows = [
        [
            ("★" if p.tag in highlight else "") + str(p.rank),
            p.name[:15],
            p.trophies,
            (getattr(p.clan, "name", None) or "—")[:12],
        ]
        for p in players
    ]
    e = discord.Embed(title=title, color=color, description=table(rows, ["#", "Name", "Cups", "Clan"]))
    e.set_footer(text=f"Page {page + 1}/{max(pages, 1)} · ★ = tracked here")
    return e


def top_clans_embed(
    *,
    title: str,
    clans: Sequence[Any],
    page: int,
    pages: int,
    highlight: set[str],
    color: discord.Color | None = None,
) -> discord.Embed:
    rows = [
        [("★" if c.tag in highlight else "") + str(c.rank), c.name[:15], c.points, c.member_count]
        for c in clans
    ]
    e = discord.Embed(title=title, color=color, description=table(rows, ["#", "Clan", "Points", "Mem"]))
    e.set_footer(text=f"Page {page + 1}/{max(pages, 1)} · ★ = linked to this server")
    return e
