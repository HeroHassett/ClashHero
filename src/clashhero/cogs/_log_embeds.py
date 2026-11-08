"""Pure embed builders for log feeds. They take plain values/rows and never touch the DB,
so they're easy to test. Colors may be overridden per subscription by outbox.send_clan_log."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

import discord

from clashhero.utils.fmt import chunk_lines, clan_link, duration, player_link, role_name, stars, table, ts

GREEN = discord.Color.green()
RED = discord.Color.red()
BLUE = discord.Color.blue()
GOLD = discord.Color.gold()
GREY = discord.Color.light_grey()

ATTRIBUTE_LABELS = {
    "level": "Clan level",
    "war_league": "War League",
    "capital_league": "Capital League",
    "capital_hall": "Capital Hall",
}


SETTING_LABELS = {
    "description": "Description",
    "type": "Clan type",
    "required_town_hall": "Required Town Hall",
    "required_trophies": "Required trophies",
    "required_builder_trophies": "Required Builder Base trophies",
    "war_frequency": "War frequency",
    "public_war_log": "Public war log",
    "family_friendly": "Family friendly",
    "location": "Location",
    "chat_language": "Chat language",
    "labels": "Labels",
}
CLAN_TYPE_LABELS = {"open": "Anyone can join", "inviteOnly": "Invite only", "closed": "Closed"}
WAR_FREQUENCY_LABELS = {
    "always": "Always",
    "moreThanOncePerWeek": "Twice a week",
    "oncePerWeek": "Once a week",
    "lessThanOncePerWeek": "Rarely",
    "never": "Never",
    "unknown": "Not set",
}
# Display order for unit kinds inside a player's upgrade list.
UNIT_KIND_ORDER = {"Hero": 0, "Equipment": 1, "Pet": 2, "Troop": 3, "Siege": 4, "Spell": 5}

# (kind, unit name, old level or None if newly unlocked, new level)
UnitChange = tuple[str, str, int | None, int]


def _get(row: Mapping[str, Any] | None, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def profile_view(tag: str) -> discord.ui.View:
    """Link-only view with a "Profile" button that opens the player in game. Link buttons need
    no persistence, so this can be attached to any log post."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Profile", style=discord.ButtonStyle.link, url=player_link(tag)))
    return view


# ------------------------------------------------------------------ clan / members


def clan_attribute_embed(clan_name: str, clan_tag: str, attribute: str, old: Any, new: Any) -> discord.Embed:
    label = ATTRIBUTE_LABELS.get(attribute, attribute)
    if isinstance(old, int) and isinstance(new, int):
        verb = "increased" if new > old else "decreased"
        desc = f"**{label}** {verb}: {old} → **{new}**"
    else:
        desc = f"**{label}** changed: {old} → **{new}**"
    e = discord.Embed(title=f"{clan_name} achievement", description=desc, color=GOLD)
    e.set_footer(text=clan_tag)
    return e


def member_join_embed(
    clan_name: str,
    member: Any,
    member_count: int,
    player_row: Mapping[str, Any] | None = None,
) -> discord.Embed:
    th = _get(player_row, "town_hall", member.town_hall)
    lines = [
        f"TH{th} · {member.trophies} trophies",
        f"Heroes: {_get(player_row, 'heroes_total', '?')} · War stars: {_get(player_row, 'war_stars', '?')}",
        f"League: {member.league or 'Unranked'}",
    ]
    e = discord.Embed(
        title=f"{member.name} joined {clan_name}",
        url=player_link(member.tag),
        description="\n".join(lines),
        color=GREEN,
    )
    e.set_footer(text=f"{member.tag} · {member_count}/50 members")
    return e


def member_leave_embed(
    clan_name: str, member: Any, member_count: int, season_donations: int | None = None
) -> discord.Embed:
    lines = [f"TH{member.town_hall} · {member.trophies} trophies · {role_name(member.role)}"]
    if season_donations is not None:
        lines.append(f"Donated this season: {season_donations}")
    e = discord.Embed(
        title=f"{member.name} left {clan_name}",
        url=player_link(member.tag),
        description="\n".join(lines),
        color=RED,
    )
    e.set_footer(text=f"{member.tag} · {member_count}/50 members")
    return e


def clan_move_embed(
    name: str,
    tag: str,
    old_clan_name: str,
    new_clan_name: str | None,
    new_clan_tag: str,
    own_clan: bool = False,
) -> discord.Embed:
    """Follow-up to a leave: where the player went. ``own_clan`` marks one of the server's clans."""
    new = f"[{new_clan_name or new_clan_tag}]({clan_link(new_clan_tag)})"
    desc = f"**{name}** (left {old_clan_name}) is now in **{new}** ({new_clan_tag})"
    if own_clan:
        desc += "\nThat's one of this server's clans."
    e = discord.Embed(description=desc, color=GREEN if own_clan else GREY)
    e.set_footer(text=tag)
    return e


def role_change_embed(clan_name: str, name: str, tag: str, old: str | None, new: str | None) -> discord.Embed:
    from clashhero.constants import ROLE_RANK

    promoted = ROLE_RANK.get(new or "", 0) > ROLE_RANK.get(old or "", 0)
    verb = "promoted" if promoted else "demoted"
    e = discord.Embed(
        description=f"**{name}** was {verb}: {role_name(old)} → **{role_name(new)}**",
        color=GREEN if promoted else RED,
    )
    e.set_footer(text=f"{clan_name} · {tag}")
    return e


def name_change_embed(tag: str, old: str, new: str) -> discord.Embed:
    e = discord.Embed(description=f"**{old}** changed name to **{new}**", color=BLUE)
    e.set_footer(text=tag)
    return e


def town_hall_embed(name: str, tag: str, old: int, new: int) -> discord.Embed:
    e = discord.Embed(
        description=f"**{name}** upgraded Town Hall {old} → **{new}** 🎉",
        url=player_link(tag),
        color=GOLD,
    )
    e.set_footer(text=tag)
    return e


def war_preference_embed(name: str, tag: str, opted_in: bool) -> discord.Embed:
    state = "opted **in** to" if opted_in else "opted **out** of"
    e = discord.Embed(description=f"**{name}** {state} war", color=GREEN if opted_in else RED)
    e.set_footer(text=tag)
    return e


def league_change_embed(
    name: str, tag: str, old: str | None, new: str | None, trophies: int
) -> discord.Embed:
    e = discord.Embed(
        description=f"**{name}**: {old or 'Unranked'} → **{new or 'Unranked'}** ({trophies} trophies)",
        color=BLUE,
    )
    e.set_footer(text=tag)
    return e


def donations_embed(clan_name: str, entries: Sequence[tuple[str, int, int]]) -> discord.Embed:
    """entries: (name, donated, received) aggregated over the batch window."""
    donated = [(n, d) for n, d, _ in entries if d > 0]
    received = [(n, r) for n, _, r in entries if r > 0]
    e = discord.Embed(title=f"{clan_name} donations", color=GREEN)
    if donated:
        e.add_field(
            name=f"Donated ({sum(d for _, d in donated)})",
            value="\n".join(f"`{d:>4}` {n}" for n, d in sorted(donated, key=lambda x: -x[1]))[:1024],
        )
    if received:
        e.add_field(
            name=f"Received ({sum(r for _, r in received)})",
            value="\n".join(f"`{r:>4}` {n}" for n, r in sorted(received, key=lambda x: -x[1]))[:1024],
        )
    return e


def clan_games_progress_embed(clan_name: str, entries: Sequence[tuple[str, int, int]]) -> discord.Embed:
    """entries: (name, points gained in batch, total)."""
    lines = [f"`+{p:>4}` **{n}** ({total}/4000)" for n, p, total in sorted(entries, key=lambda x: -x[1])]
    return discord.Embed(
        title=f"{clan_name} Clan Games progress", description="\n".join(lines)[:4000], color=GOLD
    )


# ------------------------------------------------------------------ upgrades / boosts / settings


def unit_change_text(unit: str, old: int | None, new: int) -> str:
    if old is None:
        return f"{unit} unlocked" if new <= 1 else f"{unit} unlocked (level {new})"
    return f"{unit} {old} → **{new}**"


def _pack(blocks: Sequence[Sequence[str]], limit: int, gap: str = "\n\n") -> list[str]:
    """Join blocks of lines into strings under ``limit`` chars, keeping each block together
    when it fits (``gap`` between blocks) and splitting oversized blocks by line."""
    out: list[str] = []
    cur = ""
    for block in blocks:
        text = "\n".join(block)
        pieces = [text] if len(text) <= limit else [p.rstrip("\n") for p in chunk_lines(block, limit)]
        for piece in pieces:
            if not piece:
                continue
            sep = gap if cur else ""
            if cur and len(cur) + len(sep) + len(piece) > limit:
                out.append(cur)
                cur, sep = "", ""
            cur += sep + piece
    if cur:
        out.append(cur)
    return out


def _titled(title: str, pages: Sequence[str], color: discord.Color, footer: str) -> list[discord.Embed]:
    embeds = []
    for i, page in enumerate(pages, 1):
        suffix = f" ({i}/{len(pages)})" if len(pages) > 1 else ""
        e = discord.Embed(title=f"{title}{suffix}", description=page, color=color)
        e.set_footer(text=footer)
        embeds.append(e)
    return embeds


def upgrades_embeds(
    clan_name: str,
    entries: Sequence[tuple[str, str, int, Sequence[UnitChange]]],
    limit: int = 4000,
) -> list[discord.Embed]:
    """entries: (player name, tag, town hall, unit changes) aggregated over the batch window.
    One block per player; split across several embeds when long."""
    entries = [x for x in entries if x[3]]
    if not entries:
        return []
    blocks = []
    for name, tag, th, changes in sorted(entries, key=lambda x: (-len(x[3]), x[0].lower())):
        ordered = sorted(changes, key=lambda c: (UNIT_KIND_ORDER.get(c[0], 9), c[1]))
        head = f"**[{name}]({player_link(tag)})** · TH{th}"
        blocks.append([head, *(f"• {unit_change_text(u, old, new)}" for _, u, old, new in ordered)])
    total = sum(len(x[3]) for x in entries)
    footer = f"{total} upgrade{'s' * (total != 1)} by {len(entries)} player{'s' * (len(entries) != 1)}"
    return _titled(f"{clan_name} upgrades", _pack(blocks, limit), GOLD, footer)


def super_troops_embeds(
    clan_name: str, entries: Sequence[tuple[str, str, Sequence[str]]], limit: int = 4000
) -> list[discord.Embed]:
    """entries: (player name, tag, super troops newly boosted in the batch window)."""
    entries = [x for x in entries if x[2]]
    if not entries:
        return []
    lines = [
        f"**{name}** boosted {', '.join(f'**{t}**' for t in troops)}"
        for name, _, troops in sorted(entries, key=lambda x: x[0].lower())
    ]
    total = sum(len(x[2]) for x in entries)
    footer = f"{total} boost{'s' * (total != 1)}"
    return _titled(
        f"{clan_name} super troop boosts", _pack([[ln] for ln in lines], limit, "\n"), BLUE, footer
    )


def setting_value(setting: str, value: Any) -> str:
    if value is None or value == "" or value == []:
        return "None"
    if setting == "type":
        return CLAN_TYPE_LABELS.get(value, str(value))
    if setting == "war_frequency":
        return WAR_FREQUENCY_LABELS.get(value, str(value))
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if setting == "required_town_hall":
        return f"TH{value}"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, list | tuple):
        return ", ".join(map(str, value))
    return str(value)


def _quoted_description(text: Any, limit: int = 400) -> str:
    if not text:
        return "*(empty)*"
    return discord.utils.escape_markdown(_truncate(str(text).strip(), limit)) or "*(empty)*"


def clan_settings_embed(
    clan_name: str, clan_tag: str, changes: Sequence[tuple[str, Any, Any]]
) -> discord.Embed:
    """changes: (setting, old, new); setting keys come from services.snapshots.clan_settings."""
    e = discord.Embed(title=f"{clan_name} settings changed", url=clan_link(clan_tag), color=BLUE)
    lines = []
    for setting, old, new in changes:
        label = SETTING_LABELS.get(setting, setting.replace("_", " ").capitalize())
        if setting == "description":
            e.add_field(name="Old description", value=_quoted_description(old), inline=False)
            e.add_field(name="New description", value=_quoted_description(new), inline=False)
        elif setting == "labels":
            before, after = set(old or ()), set(new or ())
            parts = []
            if added := sorted(after - before):
                parts.append("added " + ", ".join(f"**{x}**" for x in added))
            if removed := sorted(before - after):
                parts.append("removed " + ", ".join(f"**{x}**" for x in removed))
            lines.append(f"**{label}**: {'; '.join(parts) or 'reordered'}")
        else:
            lines.append(f"**{label}**: {setting_value(setting, old)} → **{setting_value(setting, new)}**")
    if lines:
        e.description = "\n".join(lines)[:4000]
    e.set_footer(text=clan_tag)
    return e


# ------------------------------------------------------------------ war


def war_attack_embed(
    war: Mapping[str, Any], attack: Mapping[str, Any], attacker: str, defender: str
) -> discord.Embed:
    ours = not attack["is_opponent"]
    icon = "⚔️" if ours else "🛡️"
    pos_a = f"#{attack['attacker_pos']}" if attack.get("attacker_pos") else ""
    pos_d = f"#{attack['defender_pos']}" if attack.get("defender_pos") else ""
    th_a = f"TH{attack['attacker_th']}" if attack.get("attacker_th") else ""
    th_d = f"TH{attack['defender_th']}" if attack.get("defender_th") else ""
    extras = []
    if attack.get("new_stars"):
        extras.append(f"+{attack['new_stars']} new ★")
    if attack.get("is_fresh"):
        extras.append("fresh")
    desc = (
        f"{icon} **{attacker}** {pos_a} {th_a} → **{defender}** {pos_d} {th_d}\n"
        f"{stars(attack['stars'])} **{attack['destruction']:.0f}%**"
        + (f" · {' · '.join(extras)}" if extras else "")
    )
    kind = "CWL" if war.get("war_type") == "cwl" else "War"
    rnd = f" round {war['cwl_round']}" if war.get("cwl_round") else ""
    e = discord.Embed(description=desc, color=GREEN if ours else RED)
    e.set_footer(
        text=f"{kind}{rnd} vs {war.get('opponent_name') or '?'} · "
        f"{war.get('clan_stars', 0)}★ – {war.get('opponent_stars', 0)}★"
    )
    return e


def war_state_embed(war: Mapping[str, Any], new_state: str) -> discord.Embed:
    kind = "CWL" if war.get("war_type") == "cwl" else "War"
    rnd = f" (round {war['cwl_round']})" if war.get("cwl_round") else ""
    opp = war.get("opponent_name") or "?"
    size = war.get("team_size") or "?"
    if new_state == "preparation":
        title = f"{kind} declared vs {opp}{rnd}"
        desc = f"{size}v{size} · battle day starts {ts(war.get('start_time'))}"
        color = BLUE
    elif new_state == "inWar":
        title = f"{kind} battle day started vs {opp}{rnd}"
        desc = f"{size}v{size} · ends {ts(war.get('end_time'))}"
        color = GOLD
    else:
        result = (war.get("result") or "ended").upper()
        title = f"{kind} vs {opp}{rnd}: {result}"
        desc = (
            f"{war.get('clan_stars', 0)}★ {war.get('clan_destruction', 0):.2f}% — "
            f"{war.get('opponent_stars', 0)}★ {war.get('opponent_destruction', 0):.2f}%"
        )
        color = {"win": GREEN, "lose": RED}.get(war.get("result"), GREY)
    return discord.Embed(title=title, description=desc, color=color)


def missed_attacks(
    members: Iterable[Mapping[str, Any]], attacks: Iterable[Mapping[str, Any]], per_member: int
) -> list[tuple[Mapping[str, Any], int]]:
    """Our members (is_opponent == 0) with fewer than ``per_member`` attacks.
    Returns (member, attacks_used), ordered by map position."""
    used: dict[str, int] = {}
    for a in attacks:
        if not a["is_opponent"]:
            used[a["attacker_tag"]] = used.get(a["attacker_tag"], 0) + 1
    out = [
        (m, used.get(m["player_tag"], 0))
        for m in members
        if not m["is_opponent"] and used.get(m["player_tag"], 0) < per_member
    ]
    return sorted(out, key=lambda x: x[0]["map_position"] or 0)


def missed_attacks_embed(
    war: Mapping[str, Any], missed: Sequence[tuple[Mapping[str, Any], int]], per_member: int
) -> discord.Embed:
    kind = "CWL" if war.get("war_type") == "cwl" else "War"
    e = discord.Embed(title=f"{kind} vs {war.get('opponent_name') or '?'}: missed attacks", color=RED)
    by_missing: dict[int, list[str]] = {}
    for m, used in missed:
        by_missing.setdefault(per_member - used, []).append(f"#{m['map_position']} {m['name']}")
    for n in sorted(by_missing, reverse=True):
        e.add_field(name=f"{n} missed", value="\n".join(by_missing[n])[:1024], inline=False)
    if not missed:
        e.description = "Everyone used all attacks. 🎉"
        e.color = GREEN
    return e


def cwl_lineup_embed(season: str, added: Sequence[str], removed: Sequence[str]) -> discord.Embed:
    e = discord.Embed(title=f"CWL {season} lineup changed", color=BLUE)
    if added:
        e.add_field(name="Added", value="\n".join(added)[:1024])
    if removed:
        e.add_field(name="Removed", value="\n".join(removed)[:1024])
    return e


# ------------------------------------------------------------------ capital


def capital_contribution_embed(name: str, tag: str, amount: int) -> discord.Embed:
    e = discord.Embed(description=f"**{name}** contributed **{amount:,}** capital gold", color=GOLD)
    e.set_footer(text=tag)
    return e


def raid_attack_embed(name: str, attacks: int, loot: int, total: int, limit: int) -> discord.Embed:
    return discord.Embed(
        description=f"**{name}** raided {attacks}x for **{loot:,}** loot ({total}/{limit} attacks)",
        color=BLUE,
    )


def raid_summary_embed(
    clan_name: str, season: Mapping[str, Any], members: Sequence[Mapping[str, Any]]
) -> discord.Embed:
    e = discord.Embed(title=f"{clan_name} raid weekend summary", color=GOLD)
    e.description = (
        f"Loot: **{_get(season, 'total_loot', 0):,}** · Attacks: {_get(season, 'attack_count', 0)} · "
        f"Districts destroyed: {_get(season, 'districts_destroyed', 0)}\n"
        f"Raid medals: offense {_get(season, 'offensive_reward', '?')} · "
        f"defense {_get(season, 'defensive_reward', '?')}"
    )
    top = sorted(members, key=lambda m: -m["loot"])[:30]
    if top:
        e.add_field(
            name="Raiders",
            value=table(
                [(m["name"][:15], f"{m['attacks']}/{m['attack_limit']}", f"{m['loot']:,}") for m in top],
                ["Name", "Atk", "Loot"],
            )[:1024],
            inline=False,
        )
    return e


def maintenance_embed(started: bool, at: datetime, since: datetime | None = None) -> discord.Embed:
    if started:
        return discord.Embed(
            title="🛠️ Clash of Clans API maintenance started",
            description=f"Started {ts(at)}. Logs, alerts and stats pause until it ends; "
            "ClashHero resumes automatically.",
            color=RED,
        )
    took = f" after {duration((at - since).total_seconds())}" if since else ""
    return discord.Embed(
        title="✅ Clash of Clans API is back",
        description=f"Maintenance ended {ts(at)}{took}. Data collection has resumed.",
        color=GREEN,
    )
