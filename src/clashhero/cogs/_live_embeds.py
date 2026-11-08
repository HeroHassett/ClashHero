"""Pure builders for live (auto-edited) embeds."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import discord

from clashhero.utils.fmt import clan_link, stars, table, ts

# Clan Games reward tiers (clan total points) and per-player cap. Adjust if Supercell changes them.
CLAN_GAMES_TIERS = (3_000, 7_500, 12_000, 18_000, 30_000, 50_000)
CLAN_GAMES_PLAYER_MAX = 4_000


def th_breakdown(members: Sequence[Mapping[str, Any]], opponent: bool) -> str:
    counts = Counter(m["town_hall"] for m in members if bool(m["is_opponent"]) == opponent and m["town_hall"])
    return " ".join(f"TH{th}×{n}" for th, n in sorted(counts.items(), reverse=True)) or "—"


def war_embed(
    war: Mapping[str, Any] | None,
    clan_name: str,
    members: Sequence[Mapping[str, Any]],
    attacks: Sequence[Mapping[str, Any]],
    names: Mapping[str, str],
) -> discord.Embed:
    if war is None:
        return discord.Embed(title=f"{clan_name}: not in war", color=discord.Color.light_grey())
    kind = "CWL" if war["war_type"] == "cwl" else "War"
    rnd = f" · round {war['cwl_round']}" if war["cwl_round"] else ""
    state = war["state"]
    e = discord.Embed(
        title=f"{kind}: {clan_name} vs {war['opponent_name'] or '?'}{rnd}",
        color={"preparation": discord.Color.blue(), "inWar": discord.Color.gold()}.get(
            state, discord.Color.green() if war["result"] == "win" else discord.Color.red()
        ),
    )
    if state == "preparation":
        status = f"Preparation · battle day {ts(war['start_time'])}"
    elif state == "inWar":
        status = f"Battle day · ends {ts(war['end_time'])}"
    else:
        status = f"Ended · **{(war['result'] or '?').upper()}**"
    per = war["attacks_per_member"] or 1
    size = war["team_size"] or 0
    e.description = (
        f"{status}\n"
        f"**{war['clan_stars']}★** {war['clan_destruction'] or 0:.2f}% — "
        f"**{war['opponent_stars']}★** {war['opponent_destruction'] or 0:.2f}%\n"
        f"Attacks: {war['clan_attacks']}/{size * per} — {war['opponent_attacks']}/{size * per}"
    )
    e.add_field(name="Our lineup", value=th_breakdown(members, False)[:1024], inline=False)
    e.add_field(name="Their lineup", value=th_breakdown(members, True)[:1024], inline=False)
    recent = sorted(attacks, key=lambda a: -a["attack_order"])[:8]
    if recent:
        lines = [
            f"{'⚔️' if not a['is_opponent'] else '🛡️'} {names.get(a['attacker_tag'], '?')[:14]} → "
            f"#{a['defender_pos'] or '?'} {stars(a['stars'])} {a['destruction']:.0f}%"
            for a in recent
        ]
        e.add_field(name="Recent attacks", value="\n".join(lines)[:1024], inline=False)
    e.set_footer(text=f"{size}v{size} · updated")
    e.timestamp = datetime.now(UTC)
    return e


def clan_games_embed(
    clan_name: str, rows: Sequence[Mapping[str, Any]], window_end: datetime
) -> discord.Embed:
    total = sum(r["points"] for r in rows)
    reached = [t for t in CLAN_GAMES_TIERS if total >= t]
    nxt = next((t for t in CLAN_GAMES_TIERS if total < t), None)
    e = discord.Embed(title=f"{clan_name} Clan Games", color=discord.Color.gold())
    e.description = (
        f"Total **{total:,}** points · tier {len(reached)}/{len(CLAN_GAMES_TIERS)}"
        + (f" · next tier at {nxt:,}" if nxt else " · max tier reached 🎉")
        + f"\nEnds {ts(window_end)}"
    )
    top = sorted(rows, key=lambda r: -r["points"])[:40]
    if top:
        body = table(
            [(i + 1, (r["name"] or r["player_tag"])[:15], r["points"]) for i, r in enumerate(top)],
            ["#", "Name", "Points"],
        )
        e.add_field(name="Leaderboard", value=body[:1024], inline=False)
    e.timestamp = datetime.now(UTC)
    return e


def last_seen_embed(clan_name: str, rows: Sequence[Mapping[str, Any]]) -> discord.Embed:
    """rows: name, last_seen (iso or None), day (activity count 24h), week (7d)."""

    def key(r):
        return r["last_seen"] or ""

    lines = []
    for r in sorted(rows, key=key, reverse=True):
        seen = _ago(r["last_seen"])
        lines.append((r["name"][:15], seen, r["day"] or 0, r["week"] or 0))
    e = discord.Embed(title=f"{clan_name}: last seen", color=discord.Color.blue())
    e.description = table(lines, ["Name", "Seen", "24h", "7d"])[:4000] if lines else "No members tracked yet."
    e.set_footer(text="Estimated from observed activity")
    e.timestamp = datetime.now(UTC)
    return e


def _ago(iso: str | None, now: datetime | None = None) -> str:
    if not iso:
        return "—"
    now = now or datetime.now(UTC)
    dt = datetime.fromisoformat(iso)
    secs = int((now - dt).total_seconds())
    if secs < 3600:
        return f"{max(secs // 60, 0)}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def clan_showcase_embed(
    clan: Mapping[str, Any] | None, members: Sequence[Mapping[str, Any]], description: str | None
) -> discord.Embed:
    if clan is None:
        return discord.Embed(title="Clan not collected yet", color=discord.Color.light_grey())
    e = discord.Embed(
        title=f"{clan['name']} ({clan['clan_tag']})",
        url=clan_link(clan["clan_tag"]),
        description=description or None,
        color=discord.Color.blurple(),
    )
    if clan["badge_url"]:
        e.set_thumbnail(url=clan["badge_url"])
    e.add_field(name="Level", value=str(clan["level"] or "?"))
    e.add_field(name="Members", value=f"{clan['member_count'] or len(members)}/50")
    e.add_field(name="War League", value=clan["war_league"] or "—")
    e.add_field(name="Capital League", value=clan["capital_league"] or "—")
    ths = Counter(m["town_hall"] for m in members if m["town_hall"])
    e.add_field(
        name="Town Halls",
        value=" ".join(f"TH{t}×{n}" for t, n in sorted(ths.items(), reverse=True)) or "—",
        inline=False,
    )
    e.timestamp = datetime.now(UTC)
    return e
