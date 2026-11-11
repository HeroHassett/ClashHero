"""Pure builders for live (auto-edited) embeds, plus the clan and Clan Games pieces that the
/clangames, /capital, /todo and /boosts displays share with them."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import discord
from discord.utils import escape_markdown

from clashhero.cogs._log_embeds import arrow, clan_author, star_icon, th_icon, war_label, war_stars
from clashhero.repos.capital import GAMES_PLAYER_CAP, GAMES_TIERS
from clashhero.services.emojis import EMOJIS
from clashhero.utils.fmt import clan_link, table, ts
from clashhero.utils.style import bar

# Clan Games reward tiers (clan total points) and per-player cap.
CLAN_GAMES_TIERS = GAMES_TIERS
CLAN_GAMES_PLAYER_MAX = GAMES_PLAYER_CAP

BLUE = discord.Color.blue()
GOLD = discord.Color.gold()
GREEN = discord.Color.green()
RED = discord.Color.red()
GREY = discord.Color.light_grey()


def _val(row: Mapping[str, Any] | None, key: str, default: Any = None) -> Any:
    """``row[key]`` for a dict or a database row, ``default`` when it's missing or NULL."""
    if row is None:
        return default
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def icon_text(*parts: str) -> str:
    """Parts joined by spaces, skipping empty ones (an icon that isn't uploaded)."""
    return " ".join(p for p in parts if p)


def war_league_icon(name: str | None) -> str:
    """The CWL league's icon ("Gold League I" -> ``cwl_gold_i``), empty when not uploaded."""
    if not name:
        return ""
    return EMOJIS.get(f"cwl_{name.lower().replace(' league', '').replace(' ', '_')}")


def clan_header(e: discord.Embed, name: str | None, tag: str | None, badge_url: str | None) -> discord.Embed:
    """Show the clan on a command's embed: its badge beside its linked name as the author. When
    the author line already carries the API maintenance notice, the badge goes in the thumbnail
    and the name in front of the title instead."""
    if not e.author.name:
        return clan_author(e, name, tag, badge_url)
    if badge_url:
        e.set_thumbnail(url=badge_url)
    if name and e.title:
        e.title = _truncate(f"{name}: {e.title}", 256)
    return e


def clan_row_header(e: discord.Embed, clan_row: Mapping[str, Any] | None, tag: str) -> discord.Embed:
    """:func:`clan_header` from the clan's stored row (just the tag when it isn't stored)."""
    return clan_header(e, _val(clan_row, "name", tag), tag, _val(clan_row, "badge_url"))


def fit_table(rows: Sequence[Sequence[object]], headers: Sequence[str], limit: int) -> str:
    """``utils.fmt.table`` cut to the first rows that fit ``limit`` characters, with
    "… and N more" under the code block when rows were left out."""
    for keep in range(len(rows), -1, -1):
        rest = len(rows) - keep
        text = table(rows[:keep], headers) + (f"\n… and {rest} more" if rest else "")
        if len(text) <= limit:
            return text
    return f"{len(rows)} rows"


def fit_names(names: Sequence[str], limit: int = 1024) -> str:
    """Names joined by commas, cut with "and N more" to stay within ``limit`` characters."""
    for keep in range(len(names), 0, -1):
        rest = len(names) - keep
        text = ", ".join(names[:keep]) + (f" and {rest} more" if rest else "")
        if len(text) <= limit:
            return text
    return f"{len(names)} players"


def th_breakdown(members: Sequence[Mapping[str, Any]], opponent: bool | None = None) -> str:
    """Town Hall icons with how many of each, highest first: ``<TH16>×5 <TH15>×3`` (``TH16×5``
    when the icons aren't uploaded). ``opponent`` picks a side of a war lineup."""
    counts = Counter(
        m["town_hall"]
        for m in members
        if _val(m, "town_hall") and (opponent is None or bool(m["is_opponent"]) == opponent)
    )
    return " ".join(f"{th_icon(th)}×{n}" for th, n in sorted(counts.items(), reverse=True)) or "—"


# ------------------------------------------------------------------ war / CWL


def _war_status(war: Mapping[str, Any], size: int) -> tuple[list[str], discord.Color]:
    state = war.get("state")
    teams = f"{size} vs {size}"
    clock = EMOJIS.get("ui_stopwatch")
    if state == "preparation":
        when = icon_text(clock, f"Battle day starts {ts(war.get('start_time'))}")
        return [f"**Preparation day** · {teams}", when], BLUE
    if state == "inWar":
        return [f"**Battle day** · {teams}", icon_text(clock, f"Ends {ts(war.get('end_time'))}")], GOLD
    result = war.get("result")
    word = {"win": "Victory", "lose": "Defeat", "tie": "Tie"}.get(result, "War ended")
    mark = arrow("up") if result == "win" else arrow("down") if result == "lose" else ""
    lines = [icon_text(mark, f"**{word}** · {teams}")]
    if war.get("end_time"):
        lines.append(f"Ended {ts(war['end_time'])}")
    return lines, {"win": GREEN, "lose": RED}.get(result, GREY)


def _attack_line(a: Mapping[str, Any], names: Mapping[str, str]) -> str:
    """``⚔ Alice → 5. Bob ★★☆ 87%``: sword for our attacks, shield for theirs."""
    icon = EMOJIS.get("ui_shield", "🛡️") if a["is_opponent"] else EMOJIS.get("ui_sword", "⚔️")
    who = escape_markdown(names.get(a["attacker_tag"], "?")[:14])
    pos = _val(a, "defender_pos")
    defender = names.get(_val(a, "defender_tag", ""))
    spot = f"**{pos}.**" if pos else ""
    target = icon_text(spot, escape_markdown(defender[:14]) if defender else "") or "?"
    result = f"{war_stars(a['stars'], _val(a, 'new_stars'))} **{a['destruction']:.0f}%**"
    return f"{icon} {who} → {target} {result}"


def _fit_recent(lines: Sequence[str], limit: int = 1024) -> str:
    """The most recent lines (first) that fit ``limit`` characters."""
    out: list[str] = []
    for line in lines:
        if len("\n".join([*out, line])) > limit:
            break
        out.append(line)
    return "\n".join(out)


def war_embed(
    war: Mapping[str, Any] | None,
    clan_name: str,
    members: Sequence[Mapping[str, Any]],
    attacks: Sequence[Mapping[str, Any]],
    names: Mapping[str, str],
    *,
    clan_tag: str | None = None,
    badge_url: str | None = None,
) -> discord.Embed:
    """The live war board: our clan (badge) as the author, the opponent (linked, its badge as
    the thumbnail) in the title, the day and its time, both clans' stars, destruction and attacks
    side by side, each lineup's Town Halls and the latest attacks."""
    if war is None:
        e = discord.Embed(
            title="Not in war", description="This updates when the next war starts.", color=GREY
        )
        clan_author(e, clan_name, clan_tag, badge_url)
        e.timestamp = datetime.now(UTC)
        return e
    war = dict(war)
    size = war.get("team_size") or 0
    per = war.get("attacks_per_member") or 1
    opponent = war.get("opponent_name") or "?"
    ours = war.get("clan_name") or clan_name
    status, color = _war_status(war, size)
    e = discord.Embed(
        title=_truncate(f"{war_label(war)} vs {opponent}", 256),
        url=clan_link(war["opponent_tag"]) if war.get("opponent_tag") else None,
        description="\n".join(status),
        color=color,
    )
    clan_author(e, ours, war.get("clan_tag") or clan_tag, war.get("clan_badge_url") or badge_url)
    if war.get("opponent_badge_url"):
        e.set_thumbnail(url=war["opponent_badge_url"])
    if war.get("state") != "preparation":
        star, sword = star_icon(), EMOJIS.get("ui_sword", "⚔️")
        for side, name in (("clan", ours), ("opponent", opponent)):
            score = f"{star} **{war.get(f'{side}_stars') or 0}** · {war.get(f'{side}_destruction') or 0:.2f}%"
            used = f"{sword} **{war.get(f'{side}_attacks') or 0}**/{size * per} attacks"
            e.add_field(name=_truncate(name, 256), value=f"{score}\n{used}")
    e.add_field(name="Our lineup", value=th_breakdown(members, False)[:1024], inline=False)
    e.add_field(name="Their lineup", value=th_breakdown(members, True)[:1024], inline=False)
    recent = sorted(attacks, key=lambda a: -a["attack_order"])[:8]
    if recent:
        e.add_field(
            name="Recent attacks", value=_fit_recent([_attack_line(a, names) for a in recent]), inline=False
        )
    e.set_footer(text="Updated")
    e.timestamp = datetime.now(UTC)
    return e


# ------------------------------------------------------------------ Clan Games


def games_summary_lines(total: int) -> list[str]:
    """The clan's points and reward tier, then a bar towards the next tier."""
    reached = sum(1 for t in CLAN_GAMES_TIERS if total >= t)
    tiers = len(CLAN_GAMES_TIERS)
    head = icon_text(EMOJIS.get("ui_clan_games"), f"**{total:,}** clan points · tier **{reached}**/{tiers}")
    if reached >= tiers:
        return [head, f"{arrow('up')} All {tiers} reward tiers unlocked"]
    floor = CLAN_GAMES_TIERS[reached - 1] if reached else 0
    nxt = CLAN_GAMES_TIERS[reached]
    return [head, f"{bar((total - floor) / (nxt - floor))} **{nxt - total:,}** to tier {reached + 1}"]


def games_window_line(starts: datetime | None, ends: datetime, now: datetime | None = None) -> str:
    """When the Clan Games start or end, with the stopwatch icon."""
    now = now or datetime.now(UTC)
    if starts is not None and now < starts:
        text = f"Starts {ts(starts)}"
    else:
        text = f"Ends {ts(ends)}" if now < ends else f"Ended {ts(ends)}"
    return icon_text(EMOJIS.get("ui_stopwatch"), text)


def games_table(rows: Sequence[Mapping[str, Any]], limit: int, cap: int = CLAN_GAMES_PLAYER_MAX) -> str:
    """Leaderboard by points (✅ once a player has hit the cap), cut to fit ``limit``."""
    data = [
        (i, (r["name"] or r["player_tag"])[:16], f"{r['points']:,}" + (" ✅" if r["points"] >= cap else ""))
        for i, r in enumerate(rows, 1)
    ]
    return fit_table(data, ["#", "PLAYER", "POINTS"], limit) if data else "No points recorded yet."


def clan_games_embed(
    clan_name: str,
    rows: Sequence[Mapping[str, Any]],
    window_end: datetime,
    *,
    window_start: datetime | None = None,
    clan_tag: str | None = None,
    badge_url: str | None = None,
) -> discord.Embed:
    total = sum(r["points"] for r in rows)
    head = [*games_summary_lines(total), games_window_line(window_start, window_end)]
    text = "\n".join(head)
    top = sorted(rows, key=lambda r: (-r["points"], r["name"] or r["player_tag"]))
    e = discord.Embed(
        title=icon_text(EMOJIS.get("ui_clan_games"), "Clan Games"),
        description=f"{text}\n\n{games_table(top, 4000 - len(text))}",
        color=GOLD,
    )
    clan_author(e, clan_name, clan_tag, badge_url)
    e.timestamp = datetime.now(UTC)
    return e


# ------------------------------------------------------------------ last seen


def last_seen_embed(
    clan_name: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    clan_tag: str | None = None,
    badge_url: str | None = None,
) -> discord.Embed:
    """rows: name, last_seen (iso or None), day (activity count 24h), week (7d)."""

    def key(r):
        return r["last_seen"] or ""

    lines = []
    for r in sorted(rows, key=key, reverse=True):
        seen = _ago(r["last_seen"])
        lines.append((r["name"][:15], seen, r["day"] or 0, r["week"] or 0))
    body = fit_table(lines, ["NAME", "SEEN", "24H", "7D"], 4000) if lines else "No members tracked yet."
    e = discord.Embed(title=icon_text(EMOJIS.get("ui_stopwatch"), "Last seen"), description=body, color=BLUE)
    clan_author(e, clan_name, clan_tag, badge_url)
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


# ------------------------------------------------------------------ clan showcase


def clan_showcase_embed(
    clan: Mapping[str, Any] | None, members: Sequence[Mapping[str, Any]], description: str | None
) -> discord.Embed:
    if clan is None:
        return discord.Embed(title="Clan not collected yet", color=GREY)
    e = discord.Embed(
        title=f"{clan['name']} ({clan['clan_tag']})",
        url=clan_link(clan["clan_tag"]),
        description=description or None,
        color=discord.Color.blurple(),
    )
    if clan["badge_url"]:
        e.set_thumbnail(url=clan["badge_url"])
    e.add_field(name="Level", value=f"**{clan['level'] or '?'}**")
    e.add_field(name="Members", value=f"**{clan['member_count'] or len(members)}**/50")
    league = clan["war_league"]
    e.add_field(name="War League", value=icon_text(war_league_icon(league), league or "—"))
    e.add_field(name="Capital League", value=clan["capital_league"] or "—")
    e.add_field(name="Town Halls", value=th_breakdown(members)[:1024], inline=False)
    e.timestamp = datetime.now(UTC)
    return e
