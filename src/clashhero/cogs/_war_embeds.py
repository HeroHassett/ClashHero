"""Shared helpers for the war / CWL / capital / clan games cogs: periods, choices and pure
embed builders (they accept coc.py objects or anything with the same attributes)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import discord
from discord import app_commands

from clashhero.repos.war import AttackSummary, DefenseSummary, PlayerWarHistory, Standing
from clashhero.utils.fmt import stars, table, ts
from clashhero.utils.resolve import resolve_player
from clashhero.utils.seasons import game_season, previous_game_season

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


def th_matchup(ours: Sequence[Any], theirs: Sequence[Any]) -> str:
    """'TH17: 5 vs 4' lines for each TH present on either side."""

    def count(ms: Sequence[Any]) -> dict[int, int]:
        out: dict[int, int] = {}
        for m in ms:
            out[m.town_hall] = out.get(m.town_hall, 0) + 1
        return out

    a, b = count(ours), count(theirs)
    ths = sorted(set(a) | set(b), reverse=True)
    return "\n".join(f"TH{th}: {a.get(th, 0)} vs {b.get(th, 0)}" for th in ths) or "—"


def war_overview_embed(war: Any, clan_tag: str, color: discord.Color) -> discord.Embed:
    ours, theirs = war_sides(war, clan_tag)
    state = state_value(war)
    per = getattr(war, "attacks_per_member", None) or (1 if getattr(war, "is_cwl", False) else 2)
    total = war.team_size * per
    title = f"{ours.name} vs {theirs.name}"
    e = discord.Embed(title=title, color=color)
    e.description = f"**{STATE_LABEL.get(state, state)}** · {war.team_size}v{war.team_size}"
    if getattr(war, "is_cwl", False):
        e.description += " · CWL"
    if state == "preparation":
        e.description += f"\nBattle day starts {ts(_when(war.start_time))}"
    elif state == "inWar":
        e.description += f"\nEnds {ts(_when(war.end_time))}"
    else:
        e.description += f"\nEnded {ts(_when(war.end_time))}"
    if state != "preparation":
        e.add_field(
            name="Score",
            value=(
                f"⭐ {ours.stars} - {theirs.stars}\n"
                f"💥 {ours.destruction:.2f}% - {theirs.destruction:.2f}%\n"
                f"⚔️ {ours.attacks_used}/{total} - {theirs.attacks_used}/{total}"
            ),
        )
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


def history_embed(name: str, label: str, h: PlayerWarHistory, color: discord.Color) -> discord.Embed:
    o, d = h.offense, h.defense
    e = discord.Embed(title=f"War history · {name}", description=f"{label} · {h.wars} wars", color=color)
    e.add_field(
        name="Offense",
        value=(
            f"Attacks: {o.attacks} (missed {o.missed})\n"
            f"3★ rate: {pct(o.triple_rate)} ({o.triples})\n"
            f"Avg stars: {o.avg_stars:.2f}\n"
            f"Avg destruction: {o.avg_destruction:.1f}%\n"
            f"Fresh-hit 3★: {pct(o.fresh_triple_rate)} ({o.fresh_triples}/{o.fresh})"
        ),
    )
    e.add_field(
        name="Defense",
        value=(
            f"Times attacked: {d.defenses}\n"
            f"Triples allowed: {d.triples_allowed}\n"
            f"Hold rate: {pct(d.hold_rate)}\n"
            f"Avg stars allowed: {d.avg_stars:.2f}"
        ),
    )
    e.add_field(name="By TH difference", value=bucket_lines(o), inline=False)
    if h.recent:
        lines = [
            f"{stars(a['stars'])} {a['destruction']:.0f}% · TH{a['attacker_th']}→TH{a['defender_th']}"
            f" vs {a['opponent_name'] or '?'}{' (CWL)' if a['war_type'] == 'cwl' else ''}"
            for a in h.recent
        ]
        e.add_field(name="Recent attacks", value="\n".join(lines)[:1024], inline=False)
    return e


def bucket_lines(o: AttackSummary) -> str:
    out = []
    for key, label in (("up", "Hitting up"), ("even", "Even"), ("down", "Hitting down")):
        n, rate = o.bucket_rate(key)
        out.append(f"{label}: {n} hits, {pct(rate)} 3★")
    return "\n".join(out)


def attack_table(rows: Sequence[tuple[str, AttackSummary]], names: dict[str, str]) -> str:
    data = [
        (
            i + 1,
            names.get(t, t)[:14],
            s.attacks,
            pct(s.triple_rate),
            f"{s.avg_stars:.2f}",
            f"{s.avg_destruction:.0f}",
            s.missed,
        )
        for i, (t, s) in enumerate(rows)
    ]
    return table(data, ["#", "Player", "Hits", "3★", "Avg★", "Dst", "Miss"])


def defense_table(rows: Sequence[tuple[str, DefenseSummary]], names: dict[str, str]) -> str:
    data = [
        (i + 1, names.get(t, t)[:14], s.defenses, s.triples_allowed, pct(s.hold_rate), f"{s.avg_stars:.2f}")
        for i, (t, s) in enumerate(rows)
    ]
    return table(data, ["#", "Player", "Def", "3★ allowed", "Hold", "Avg★"])


def standings_table(rows: Sequence[Standing]) -> str:
    data = [
        (i + 1, s.name[:15], s.total_stars, f"{s.destruction:.0f}", f"{s.wins}-{s.losses}-{s.ties}")
        for i, s in enumerate(rows)
    ]
    return table(data, ["#", "Clan", "★", "Dest", "W-L-T"])
