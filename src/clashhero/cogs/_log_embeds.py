"""Pure embed builders for log feeds. They take plain values/rows and never touch the DB,
so they're easy to test. Colors may be overridden per subscription by outbox.send_clan_log."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import discord
from discord.utils import escape_markdown

from clashhero.cogs._info_embeds import rush_report, unit_levels
from clashhero.constants import CLAN_TYPE_LABELS, WAR_FREQUENCY_LABELS
from clashhero.services.emojis import EMOJIS
from clashhero.utils.fmt import chunk_lines, clan_link, duration, player_link, role_name, ts
from clashhero.utils.ranked import league_name, tier_number
from clashhero.utils.style import ICONS

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


def _arrow(direction: str, color: str = "") -> str:
    """The bot's coloured arrow emoji (green ▲, red ▼, or blue ▲), else the plain character."""
    name = f"ui_arrow_{direction}" + (f"_{color}" if color else "")
    return EMOJIS.get(name, "▲" if direction == "up" else "▼")


arrow = _arrow  # public: other embed modules colour their arrows the same way


def th_icon(level: int | None) -> str:
    """The Town Hall's game icon, else "TH18"; empty when unknown."""
    if not level:
        return ""
    return EMOJIS.town_hall(level) or f"TH{level}"


def player_ref(name: str, tag: str) -> str:
    """The player's name as a link to their in-game profile."""
    return f"[{escape_markdown(name)}]({player_link(tag)})"


def war_stars(stars: int, new: int | None = None) -> str:
    """Three star icons: silver for stars an earlier attack already took, gold for new ones,
    empty for the rest (all gold when ``new`` isn't known)."""
    stars = max(0, min(3, stars))
    new = stars if new is None else max(0, min(stars, new))
    old_star = EMOJIS.get("ui_star_old", "★")
    new_star = EMOJIS.get("ui_star_new", "★")
    empty = EMOJIS.get("ui_star_empty", "☆")
    return old_star * (stars - new) + new_star * new + empty * (3 - stars)


def star_icon() -> str:
    return EMOJIS.get("ui_war_star", "★")


def clan_author(e: discord.Embed, name: str | None, tag: str | None, badge_url: str | None) -> discord.Embed:
    """The clan as the embed's author line: its badge beside its name, linked to it in game."""
    if name or tag:
        e.set_author(name=name or tag or "", url=clan_link(tag) if tag else None, icon_url=badge_url or None)
    return e


def profile_view(tag: str) -> discord.ui.View:
    """Link-only view with a "Profile" button that opens the player in game. Link buttons need
    no persistence, so this can be attached to any log post."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Profile", style=discord.ButtonStyle.link, url=player_link(tag)))
    return view


# ------------------------------------------------------------------ clan / members


# War leagues lowest first, for telling a promotion from a drop.
WAR_LEAGUES = tuple(
    f"{metal} League {n}"
    for metal in ("Bronze", "Silver", "Gold", "Crystal", "Master", "Champion")
    for n in ("III", "II", "I")
)


def _war_league_icon(name: Any) -> str:
    return EMOJIS.get(f"cwl_{str(name).lower().replace(' league', '').replace(' ', '_')}") if name else ""


def clan_attribute_embed(
    clan_name: str,
    clan_tag: str,
    attribute: str,
    old: Any,
    new: Any,
    *,
    badge_url: str | None = None,
    at: datetime | None = None,
) -> discord.Embed:
    """A clan milestone (level, war or capital league, Capital Hall): the clan as the author,
    what it is now with an up or down arrow, and what it was."""
    label = ATTRIBUTE_LABELS.get(attribute, attribute)
    if isinstance(old, int) and isinstance(new, int):
        up = new > old
    elif attribute == "war_league" and old in WAR_LEAGUES and new in WAR_LEAGUES:
        up = WAR_LEAGUES.index(new) > WAR_LEAGUES.index(old)
    else:
        up = None
    arrow = "" if up is None else _arrow("up" if up else "down")
    icon_new = _war_league_icon(new) if attribute == "war_league" else ""
    icon_old = _war_league_icon(old) if attribute == "war_league" else ""
    value_new = f"{new:,}" if isinstance(new, int) else str(new or "None")
    value_old = f"{old:,}" if isinstance(old, int) else str(old or "None")
    lines = [
        " ".join(x for x in (arrow, f"{label}:", icon_new, f"**{value_new}**") if x),
        " ".join(x for x in ("was", icon_old, value_old) if x),
    ]
    e = discord.Embed(description="\n".join(lines), color=GREEN if up else RED if up is False else GOLD)
    clan_author(e, clan_name, clan_tag, badge_url)
    e.timestamp = at
    return e


@dataclass(frozen=True)
class JoinStats:
    """What a join post shows beyond the clan's member list (from a fresh /players fetch)."""

    town_hall: int
    trophies: int
    league: str | None
    heroes: tuple[tuple[str, int], ...]  # (name, level), home village, in game order
    war_stars: int
    rushed: float | None  # % of levels missing vs the previous Town Hall's max; None if unknown


def join_stats(player: Any) -> JoinStats:
    """JoinStats from a coc.py Player (its raw payload when there is one)."""
    raw = getattr(player, "_raw_data", None)
    if isinstance(raw, dict):
        heroes = tuple(
            (h["name"], int(h.get("level") or 0))
            for h in raw.get("heroes") or []
            if h.get("name") and h.get("village", "home") == "home"
        )
    else:
        heroes = tuple((h.name, h.level) for h in getattr(player, "heroes", None) or [])
    try:
        overall = rush_report(unit_levels(player)).overall
        rushed = overall.percent if overall.total else None
    except Exception:  # unknown game data: leave it out rather than fail the post
        rushed = None
    return JoinStats(
        town_hall=player.town_hall or 0,
        trophies=player.trophies or 0,
        league=league_name(player),
        heroes=heroes,
        war_stars=player.war_stars or 0,
        rushed=rushed,
    )


def _icon_stat(icon: str, value: object, fallback: str) -> str:
    """``{icon}**18**``, or ``TH **18**`` when the icon isn't uploaded."""
    return f"{icon}**{value}**" if icon else f"{fallback} **{value}**"


def _member_head(town_hall: int, league: str | None, trophies: int) -> list[str]:
    trophy = EMOJIS.league(league) or EMOJIS.get("res_trophy", "🏆")
    return [_icon_stat(EMOJIS.town_hall(town_hall), town_hall, "TH"), _icon_stat(trophy, f"{trophies:,}", "")]


def _member_embed(member: Any, lines: Sequence[str], color: discord.Color) -> discord.Embed:
    return player_embed(member.name, member.tag, lines, color)


def player_embed(name: str, tag: str, lines: Sequence[str], color: discord.Color) -> discord.Embed:
    """A post about one player: "Name (#TAG)" as the title, linked to their in-game profile."""
    return discord.Embed(
        title=_truncate(f"{escape_markdown(name)} ({tag})", 256),
        url=player_link(tag),
        description="\n".join(x for x in lines if x),
        color=color,
    )


def _profile_line(town_hall: int | None, league: str | None, trophies: int | None) -> str:
    """Town Hall and trophies with their game icons, when the bot has the player's profile."""
    return " ".join(_member_head(town_hall, league, trophies or 0)) if town_hall else ""


def _clan_footer(e: discord.Embed, text: str, badge_url: str | None, at: datetime | None) -> discord.Embed:
    e.set_footer(text=text, icon_url=badge_url or None)
    e.timestamp = at
    return e


def member_join_embed(
    clan_name: str,
    member: Any,
    member_count: int,
    stats: JoinStats | None = None,
    *,
    badge_url: str | None = None,
    at: datetime | None = None,
) -> discord.Embed:
    """Town Hall, trophies and hero levels; then war stars and rushed %. Without ``stats`` (the
    player couldn't be fetched) only what the clan's member list has."""
    if stats is None:
        lines = [" ".join(_member_head(member.town_hall, member.league, member.trophies))]
    else:
        head = _member_head(stats.town_hall, stats.league or member.league, stats.trophies)
        head += [_icon_stat(EMOJIS.unit(name), level, name) for name, level in stats.heroes]
        more = [_icon_stat(EMOJIS.get("ui_war_star", "⭐"), f"{stats.war_stars:,}", "War stars")]
        if stats.rushed is not None:
            more.append(f"{ICONS['rushed']} **{stats.rushed:.2f}%** rushed")
        lines = [" ".join(head), " ".join(more)]
    e = _member_embed(member, lines, GREEN)
    return _clan_footer(e, f"Joined {clan_name} [{member_count}/50]", badge_url, at)


def member_leave_embed(
    clan_name: str,
    member: Any,
    member_count: int,
    donated: int = 0,
    received: int = 0,
    *,
    badge_url: str | None = None,
    at: datetime | None = None,
) -> discord.Embed:
    """Town Hall, trophies, and troops donated ▲ / received ▼ in this clan this season."""
    castle = EMOJIS.get("ui_donate_troops", "🏰")
    line = _member_head(member.town_hall, member.league, member.trophies)
    line.append(f"{castle}**{donated:,}** {_arrow('up')} **{received:,}** {_arrow('down')}")
    e = _member_embed(member, [" ".join(line)], RED)
    return _clan_footer(e, f"Left {clan_name} [{member_count}/50]", badge_url, at)


def with_new_clan(
    embed: discord.Embed, new_clan_name: str | None, new_clan_tag: str, own_clan: bool = False
) -> discord.Embed:
    """A posted leave, once we know where the player went: "Joined <clan>" under "Left <clan>"."""
    e = embed.copy()
    text = f"{e.footer.text or ''}\nJoined {new_clan_name or new_clan_tag} ({new_clan_tag})"
    if own_clan:
        text += " · one of this server's clans"
    e.set_footer(text=text.strip(), icon_url=e.footer.icon_url)
    return e


def clan_move_embed(
    name: str,
    tag: str,
    old_clan_name: str,
    new_clan_name: str | None,
    new_clan_tag: str,
    own_clan: bool = False,
    *,
    at: datetime | None = None,
) -> discord.Embed:
    """Follow-up to a leave: where the player went. ``own_clan`` marks one of the server's clans."""
    new = f"[{escape_markdown(new_clan_name or new_clan_tag)}]({clan_link(new_clan_tag)})"
    lines = [f"Left **{escape_markdown(old_clan_name)}** · now in **{new}** `{new_clan_tag}`"]
    if own_clan:
        lines.append(f"{_arrow('up')} That's one of this server's clans.")
    e = player_embed(name, tag, lines, GREEN if own_clan else GREY)
    e.timestamp = at
    return e


def role_change_embed(
    clan_name: str,
    name: str,
    tag: str,
    old: str | None,
    new: str | None,
    *,
    town_hall: int | None = None,
    trophies: int | None = None,
    league: str | None = None,
    badge_url: str | None = None,
    at: datetime | None = None,
) -> discord.Embed:
    """Laid out like the join and leave posts: the player (linked to their profile) as the title,
    the new role with the old one after it, their Town Hall and trophies when known, and the clan
    with its badge and the time in the footer. Green for a promotion, red for a demotion."""
    from clashhero.constants import ROLE_RANK

    promoted = ROLE_RANK.get(new or "", 0) > ROLE_RANK.get(old or "", 0)
    arrow = _arrow("up" if promoted else "down")
    verb = "Promoted" if promoted else "Demoted"
    lines = [f"{arrow} {verb} to **{role_name(new)}** · was {role_name(old)}"]
    if town_hall:
        lines.append(" ".join(_member_head(town_hall, league, trophies or 0)))
    e = discord.Embed(
        title=f"{escape_markdown(name)} ({tag})",
        url=player_link(tag),
        description="\n".join(lines),
        color=GREEN if promoted else RED,
    )
    return _clan_footer(e, clan_name, badge_url, at)


@dataclass(frozen=True)
class PlayerContext:
    """What a player post shows besides the change itself: the player's stored Town Hall, league
    and trophies, and their clan (badge and name, in the footer)."""

    town_hall: int | None = None
    league: str | None = None
    trophies: int | None = None
    clan_name: str | None = None
    badge_url: str | None = None
    at: datetime | None = None


def _player_post(
    name: str, tag: str, lines: Sequence[str], color: discord.Color, ctx: PlayerContext | None
) -> discord.Embed:
    ctx = ctx or PlayerContext()
    e = player_embed(name, tag, [*lines, _profile_line(ctx.town_hall, ctx.league, ctx.trophies)], color)
    if ctx.clan_name:
        e.set_footer(text=ctx.clan_name, icon_url=ctx.badge_url or None)
    e.timestamp = ctx.at
    return e


def name_change_embed(tag: str, old: str, new: str, ctx: PlayerContext | None = None) -> discord.Embed:
    return _player_post(new, tag, [f"Changed their name from **{escape_markdown(old)}**"], BLUE, ctx)


def town_hall_embed(
    name: str, tag: str, old: int, new: int, ctx: PlayerContext | None = None
) -> discord.Embed:
    line = f"{_arrow('up')} Upgraded to **Town Hall {new}** · was {old}"
    icons = f"{th_icon(old)} → {th_icon(new)}" if EMOJIS.town_hall(new) else ""
    ctx = dataclasses.replace(ctx or PlayerContext(), town_hall=None)  # the icons above say it
    return _player_post(name, tag, [line, icons], GOLD, ctx)


def war_preference_embed(
    name: str, tag: str, opted_in: bool, ctx: PlayerContext | None = None
) -> discord.Embed:
    line = f"{_arrow('up')} Opted **in** to war" if opted_in else f"{_arrow('down')} Opted **out** of war"
    return _player_post(name, tag, [line], GREEN if opted_in else RED, ctx)


def league_change_embed(
    name: str,
    tag: str,
    old: str | None,
    new: str | None,
    trophies: int,
    ctx: PlayerContext | None = None,
) -> discord.Embed:
    """A new league tier: up (green) or down (red) when the tiers' order is known."""
    before, after = tier_number(old), tier_number(new)
    up = before is not None and after is not None and after > before
    down = before is not None and after is not None and after < before
    arrow = _arrow("up") if up else _arrow("down") if down else ""
    verb = "Promoted to" if up else "Dropped to" if down else "Now in"
    old_icon, new_icon = EMOJIS.league(old), EMOJIS.league(new)
    lines = [
        " ".join(x for x in (arrow, verb, new_icon, f"**{new or 'Unranked'}**") if x),
        " ".join(x for x in ("was", old_icon, old or "Unranked") if x),
    ]
    ctx = dataclasses.replace(ctx or PlayerContext(), league=new, trophies=trophies)
    return _player_post(name, tag, lines, GREEN if up else RED if down else BLUE, ctx)


@dataclass(frozen=True)
class Donation:
    """One player's donations over a batch window. ``in_clan``: still a member (a name is struck
    through once they've left)."""

    name: str
    donated: int
    received: int
    town_hall: int | None = None
    in_clan: bool = True


def donations_embed(
    clan_name: str,
    clan_tag: str,
    entries: Sequence[Donation],
    *,
    numbers: Mapping[str, Mapping[int, str]] | None = None,
    badge_url: str | None = None,
    member_count: int | None = None,
    at: datetime | None = None,
) -> discord.Embed:
    """The clan (linked, its badge as the thumbnail), then who donated and who received, most
    first: each line a Town Hall icon, the amount and the name. ``numbers``: coloured number
    emojis by kind ("d" donated, "r" received) and amount; an amount without one is bold text."""
    numbers = numbers or {}

    def line(d: Donation, amount: int, kind: str) -> str:
        th = EMOJIS.town_hall(d.town_hall) if d.town_hall else ""
        number = numbers.get(kind, {}).get(amount) or f"**{amount:,}**"
        name = escape_markdown(d.name)
        return " ".join(x for x in (th, number, name if d.in_clan else f"~~{name}~~") if x)

    sections = []
    for kind, label, arrow, amount in (
        ("d", "Donated", _arrow("up", "blue"), lambda d: d.donated),
        ("r", "Received", _arrow("down"), lambda d: d.received),
    ):
        rows = sorted((d for d in entries if amount(d) > 0), key=lambda d: (-amount(d), d.name.lower()))
        if rows:
            sections.append((f"{arrow} **{label}**", [line(d, amount(d), kind) for d in rows]))
    e = discord.Embed(
        title=f"{clan_name} ({clan_tag})",
        url=clan_link(clan_tag),
        description=_fit_sections(sections, 4000),
        color=BLUE,
    )
    if badge_url:
        e.set_thumbnail(url=badge_url)
    return _clan_footer(e, f"{member_count}/50" if member_count is not None else clan_name, badge_url, at)


def _fit_sections(sections: Sequence[tuple[str, list[str]]], limit: int) -> str:
    """Headed sections separated by a blank line, dropping each section's last lines (noting
    how many) until it all fits ``limit`` characters."""
    keep = [len(lines) for _, lines in sections]

    def render() -> str:
        parts = []
        for (head, lines), n in zip(sections, keep, strict=True):
            body = lines[:n] + ([f"… and {len(lines) - n} more"] if n < len(lines) else [])
            parts.append("\n".join([head, *body]))
        return "\n\n".join(parts)

    text = render()
    while len(text) > limit and any(keep):
        longest = max(range(len(keep)), key=lambda i: keep[i])
        keep[longest] -= 1
        text = render()
    return text


@dataclass(frozen=True)
class GamesProgress:
    """One player's Clan Games points over a batch window and their total so far."""

    name: str
    tag: str
    points: int
    total: int
    town_hall: int | None = None


CLAN_GAMES_MAX = 4000  # points one player can earn


def clan_games_progress_embed(
    clan_name: str,
    entries: Sequence[GamesProgress],
    *,
    clan_tag: str | None = None,
    badge_url: str | None = None,
    at: datetime | None = None,
) -> discord.Embed:
    """Who earned Clan Games points, most first: Town Hall icon, points gained, linked name and
    their total so far (a check once they've maxed)."""
    icon = EMOJIS.get("ui_clan_games")
    lines = []
    for p in sorted(entries, key=lambda x: (-x.points, x.name.lower())):
        total = f"{p.total:,}/{CLAN_GAMES_MAX:,}" + (f" {_arrow('up')}" if p.total >= CLAN_GAMES_MAX else "")
        lines.append(
            " ".join(
                x
                for x in (th_icon(p.town_hall), f"**+{p.points:,}**", player_ref(p.name, p.tag), f"· {total}")
                if x
            )
        )
    e = discord.Embed(
        title=" ".join(x for x in (icon, "Clan Games progress") if x),
        description=_fit_lines(lines, 4000),
        color=GOLD,
    )
    clan_author(e, clan_name, clan_tag, badge_url)
    e.timestamp = at
    return e


# ------------------------------------------------------------------ upgrades / boosts / settings


def unit_change_text(unit: str, old: int | None, new: int) -> str:
    if old is None:
        return f"{unit} unlocked" if new <= 1 else f"{unit} unlocked (level {new})"
    return f"{unit} {old} → **{new}**"


def _unit_line(unit: str, old: int | None, new: int) -> str:
    """An upgrade with the unit's game icon (a bullet when it has none)."""
    return f"{EMOJIS.unit(unit) or '•'} {unit_change_text(unit, old, new)}"


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


def _titled(
    title: str,
    pages: Sequence[str],
    color: discord.Color,
    footer: str,
    *,
    clan: tuple[str | None, str | None, str | None] | None = None,
    at: datetime | None = None,
) -> list[discord.Embed]:
    """``pages`` as embeds titled ``title`` ("(2/3)" when split), the clan (name, tag, badge) as
    the author and the time on each."""
    embeds = []
    for i, page in enumerate(pages, 1):
        suffix = f" ({i}/{len(pages)})" if len(pages) > 1 else ""
        e = discord.Embed(title=f"{title}{suffix}", description=page, color=color)
        if clan is not None:
            clan_author(e, *clan)
        e.set_footer(text=footer)
        e.timestamp = at
        embeds.append(e)
    return embeds


def upgrades_embeds(
    clan_name: str,
    entries: Sequence[tuple[str, str, int, Sequence[UnitChange]]],
    limit: int = 4000,
    *,
    clan_tag: str | None = None,
    badge_url: str | None = None,
    at: datetime | None = None,
) -> list[discord.Embed]:
    """entries: (player name, tag, town hall, unit changes) aggregated over the batch window.
    One block per player (linked name and Town Hall icon, then each unit with its icon); split
    across several embeds when long."""
    entries = [x for x in entries if x[3]]
    if not entries:
        return []
    blocks = []
    for name, tag, th, changes in sorted(entries, key=lambda x: (-len(x[3]), x[0].lower())):
        ordered = sorted(changes, key=lambda c: (UNIT_KIND_ORDER.get(c[0], 9), c[1]))
        head = " ".join(x for x in (th_icon(th), f"**{player_ref(name, tag)}**") if x)
        blocks.append([head, *(_unit_line(u, old, new) for _, u, old, new in ordered)])
    total = sum(len(x[3]) for x in entries)
    footer = f"{total} upgrade{'s' * (total != 1)} by {len(entries)} player{'s' * (len(entries) != 1)}"
    return _titled(
        "Upgrades", _pack(blocks, limit), GOLD, footer, clan=(clan_name, clan_tag, badge_url), at=at
    )


def super_troops_embeds(
    clan_name: str,
    entries: Sequence[tuple[str, str, Sequence[str]]],
    limit: int = 4000,
    *,
    clan_tag: str | None = None,
    badge_url: str | None = None,
    at: datetime | None = None,
) -> list[discord.Embed]:
    """entries: (player name, tag, super troops newly boosted in the batch window)."""
    entries = [x for x in entries if x[2]]
    if not entries:
        return []
    lines = [
        f"**{player_ref(name, tag)}** boosted "
        + ", ".join(" ".join(x for x in (EMOJIS.unit(t), f"**{t}**") if x) for t in troops)
        for name, tag, troops in sorted(entries, key=lambda x: x[0].lower())
    ]
    total = sum(len(x[2]) for x in entries)
    footer = f"{total} boost{'s' * (total != 1)}"
    return _titled(
        "Super troop boosts",
        _pack([[ln] for ln in lines], limit, "\n"),
        BLUE,
        footer,
        clan=(clan_name, clan_tag, badge_url),
        at=at,
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
    clan_name: str,
    clan_tag: str,
    changes: Sequence[tuple[str, Any, Any]],
    *,
    badge_url: str | None = None,
    at: datetime | None = None,
) -> discord.Embed:
    """changes: (setting, old, new); setting keys come from services.snapshots.clan_settings."""
    e = discord.Embed(title="Clan settings changed", color=BLUE)
    clan_author(e, clan_name, clan_tag, badge_url)
    e.timestamp = at
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
    return e


# ------------------------------------------------------------------ war


def war_label(war: Mapping[str, Any]) -> str:
    """ "CWL round 7", "Friendly war" or "War"."""
    if war.get("war_type") == "cwl":
        return f"CWL round {war['cwl_round']}" if war.get("cwl_round") else "CWL"
    return "Friendly war" if war.get("war_type") == "friendly" else "War"


def _war_scores(e: discord.Embed, war: Mapping[str, Any]) -> None:
    """Both clans' stars and destruction, side by side, ours first."""
    star = star_icon()
    for side, prefix in (("clan", "clan"), ("opponent", "opponent")):
        name = war.get(f"{side}_name") or ("Us" if side == "clan" else "Opponent")
        stars = war.get(f"{prefix}_stars") or 0
        destruction = war.get(f"{prefix}_destruction") or 0
        e.add_field(name=_truncate(name, 100), value=f"{star} **{stars}** · {destruction:.2f}%")


def _war_footer(e: discord.Embed, war: Mapping[str, Any], text: str, at: datetime | None) -> discord.Embed:
    """The opponent's badge with "vs <opponent>" and ``text``, and the time."""
    vs = f"vs {war.get('opponent_name') or '?'}"
    e.set_footer(text=" · ".join(x for x in (vs, text) if x), icon_url=war.get("opponent_badge_url") or None)
    e.timestamp = at
    return e


def war_attack_embed(
    war: Mapping[str, Any],
    attack: Mapping[str, Any],
    attacker: str,
    defender: str,
    *,
    at: datetime | None = None,
) -> discord.Embed:
    """One war attack: our clan (badge) as the author; who attacked whom, each with their map
    position, linked name and Town Hall; the stars (silver = already taken, gold = new) and
    destruction; then both clans' totals, and the opponent in the footer. Green for our attack,
    red for theirs."""
    ours = not attack["is_opponent"]
    icon = EMOJIS.get("ui_sword", "⚔️") if ours else EMOJIS.get("ui_shield", "🛡️")

    def side(name: str, tag: str | None, pos: Any, th: Any) -> str:
        who = player_ref(name, tag) if tag else f"**{escape_markdown(name)}**"
        return " ".join(x for x in (f"**{pos}.**" if pos else "", who, th_icon(th)) if x)

    hitter = side(attacker, attack.get("attacker_tag"), attack.get("attacker_pos"), attack.get("attacker_th"))
    target = side(defender, attack.get("defender_tag"), attack.get("defender_pos"), attack.get("defender_th"))
    line = f"{icon} {hitter}  →  {target}"
    result = f"{war_stars(attack['stars'], attack.get('new_stars'))} **{attack['destruction']:.0f}%**"
    if attack.get("is_fresh"):
        result += " · first hit on this base"
    e = discord.Embed(description=f"{line}\n{result}", color=GREEN if ours else RED)
    clan_author(e, war.get("clan_name"), war.get("clan_tag"), war.get("clan_badge_url"))
    _war_scores(e, war)
    total = (war.get("team_size") or 0) * (war.get("attacks_per_member") or 0)
    used = war.get("clan_attacks" if ours else "opponent_attacks") or 0
    attacks = f"{used}/{total} attacks" if total else ""
    return _war_footer(e, war, " · ".join(x for x in (war_label(war), attacks) if x), at)


def war_state_embed(war: Mapping[str, Any], new_state: str, *, at: datetime | None = None) -> discord.Embed:
    """A war's preparation day, battle day or result: our clan as the author, the opponent
    (linked, its badge as the thumbnail) in the title, the times, and the score once it's on."""
    opp = war.get("opponent_name") or "?"
    size = war.get("team_size") or "?"
    label = war_label(war)
    if new_state == "preparation":
        title = f"{label} vs {opp}"
        desc = f"**Preparation day** · {size} vs {size}\nBattle day starts {ts(war.get('start_time'), 'F')}"
        color = BLUE
    elif new_state == "inWar":
        title = f"{label} vs {opp}"
        desc = f"**Battle day** · {size} vs {size}\nEnds {ts(war.get('end_time'), 'R')}"
        color = GOLD
    else:
        result = war.get("result")
        word = {"win": "Victory", "lose": "Defeat", "tie": "Tie"}.get(result, "War ended")
        arrow = _arrow("up") if result == "win" else _arrow("down") if result == "lose" else ""
        title = f"{label} vs {opp}"
        desc = " ".join(x for x in (arrow, f"**{word}**", f"· {size} vs {size}") if x)
        color = {"win": GREEN, "lose": RED}.get(result, GREY)
    e = discord.Embed(
        title=_truncate(title, 256),
        url=clan_link(war["opponent_tag"]) if war.get("opponent_tag") else None,
        description=desc,
        color=color,
    )
    clan_author(e, war.get("clan_name"), war.get("clan_tag"), war.get("clan_badge_url"))
    if war.get("opponent_badge_url"):
        e.set_thumbnail(url=war["opponent_badge_url"])
    if new_state != "preparation":
        _war_scores(e, war)
    e.set_footer(text=label)
    e.timestamp = at
    return e


# How long after a war changes state its announcement still counts as news. A change the bot only
# notices later (it was down) isn't announced as if it had just happened.
STATE_NEWS_WINDOW = timedelta(minutes=30)


def state_is_news(war: Mapping[str, Any], new_state: str, now: datetime) -> bool:
    """Whether a preparation or battle day start is recent enough to announce; results always are."""
    key = {"preparation": "preparation_start", "inWar": "start_time"}.get(new_state)
    if key is None or not war.get(key):
        return True
    started = datetime.fromisoformat(war[key])
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return now - started <= STATE_NEWS_WINDOW


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
    war: Mapping[str, Any],
    missed: Sequence[tuple[Mapping[str, Any], int]],
    per_member: int,
    *,
    at: datetime | None = None,
) -> discord.Embed:
    """Who didn't use all their attacks, grouped by how many they missed: map position, linked
    name and Town Hall."""
    e = discord.Embed(title="Missed attacks", color=RED)
    clan_author(e, war.get("clan_name"), war.get("clan_tag"), war.get("clan_badge_url"))
    by_missing: dict[int, list[str]] = {}
    for m, used in missed:
        who = player_ref(m["name"], m["player_tag"]) if m.get("player_tag") else escape_markdown(m["name"])
        line = " ".join(x for x in (f"**{m['map_position']}.**", th_icon(m.get("town_hall")), who) if x)
        by_missing.setdefault(per_member - used, []).append(line)
    for n in sorted(by_missing, reverse=True):
        noun = "attack" if n == 1 else "attacks"
        e.add_field(
            name=f"{_arrow('down')} {n} {noun} missed", value=_fit_lines(by_missing[n], 1024), inline=False
        )
    if not missed:
        e.description = f"{_arrow('up')} Everyone used all their attacks."
        e.color = GREEN
    return _war_footer(e, war, war_label(war), at)


def _fit_lines(lines: Sequence[str], limit: int) -> str:
    """Lines joined, cut with "… and N more" to stay within ``limit`` characters."""
    for keep in range(len(lines), -1, -1):
        rest = len(lines) - keep
        text = "\n".join([*lines[:keep], *([f"… and {rest} more"] if rest else [])])
        if len(text) <= limit:
            return text
    return f"{len(lines)} players"


@dataclass(frozen=True)
class LineupPlayer:
    name: str
    tag: str
    town_hall: int | None = None


def cwl_lineup_embed(
    season: str,
    added: Sequence[LineupPlayer],
    removed: Sequence[LineupPlayer],
    *,
    clan_name: str | None = None,
    clan_tag: str | None = None,
    badge_url: str | None = None,
    at: datetime | None = None,
) -> discord.Embed:
    """Players added to and removed from the clan's CWL roster: Town Hall icon and linked name."""
    e = discord.Embed(title=f"CWL {season} lineup changed", color=BLUE)
    clan_author(e, clan_name, clan_tag, badge_url)
    for label, arrow, players in (("Added", _arrow("up"), added), ("Removed", _arrow("down"), removed)):
        if players:
            lines = [
                " ".join(x for x in (th_icon(p.town_hall), player_ref(p.name, p.tag)) if x) for p in players
            ]
            e.add_field(name=f"{arrow} {label}", value=_fit_lines(lines, 1024))
    e.timestamp = at
    return e


# ------------------------------------------------------------------ capital


def capital_contribution_embed(
    name: str, tag: str, amount: int, ctx: PlayerContext | None = None
) -> discord.Embed:
    gold = EMOJIS.get("res_capital_gold")
    line = " ".join(x for x in ("Contributed", gold, f"**{amount:,}**", "Capital Gold") if x)
    return _player_post(name, tag, [line], GOLD, ctx)


def raid_attack_embed(
    name: str,
    attacks: int,
    loot: int,
    total: int,
    limit: int,
    *,
    tag: str | None = None,
    ctx: PlayerContext | None = None,
) -> discord.Embed:
    gold = EMOJIS.get("res_capital_gold")
    noun = "attack" if attacks == 1 else "attacks"
    lines = [
        " ".join(
            x for x in (EMOJIS.get("ui_sword"), f"**{attacks}** {noun} ·", gold, f"**{loot:,}** looted") if x
        ),
        f"{total}/{limit} attacks used this raid weekend",
    ]
    if tag:
        return _player_post(name, tag, lines, BLUE, ctx)
    return discord.Embed(title=escape_markdown(name), description="\n".join(lines), color=BLUE)


def raid_summary_embed(
    clan_name: str,
    season: Mapping[str, Any],
    members: Sequence[Mapping[str, Any]],
    *,
    clan_tag: str | None = None,
    badge_url: str | None = None,
    at: datetime | None = None,
) -> discord.Embed:
    """A raid weekend's totals with their icons, then the raiders by loot."""
    gold, medal = EMOJIS.get("res_capital_gold"), EMOJIS.get("res_raid_medal")
    sword = EMOJIS.get("ui_sword")

    def stat(icon: str, value: Any, label: str) -> str:
        shown = f"{value:,}" if isinstance(value, int) else str(value)
        return " ".join(x for x in (icon, f"**{shown}**", label) if x)

    lines = [
        stat(gold, _get(season, "total_loot", 0), "Capital Gold looted"),
        " · ".join(
            (
                stat(sword, _get(season, "attack_count", 0), "attacks"),
                stat("", _get(season, "districts_destroyed", 0), "districts destroyed"),
            )
        ),
        stat(medal, _get(season, "offensive_reward", "?"), "offense medals")
        + " · "
        + stat("", _get(season, "defensive_reward", "?"), "defense medals"),
    ]
    raiders = [
        " ".join(x for x in (f"**{i}.**", escape_markdown(m["name"]), "·", gold, f"**{m['loot']:,}**") if x)
        + f" · {m['attacks']}/{m['attack_limit']}"
        for i, m in enumerate(sorted(members, key=lambda m: -m["loot"])[:30], 1)
    ]
    e = discord.Embed(title="Raid weekend summary", description="\n".join(lines), color=GOLD)
    clan_author(e, clan_name, clan_tag, badge_url)
    if raiders:
        e.add_field(
            name="Raiders", value=_fit_lines([r.replace("  ", " ") for r in raiders], 1024), inline=False
        )
    e.timestamp = at
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
