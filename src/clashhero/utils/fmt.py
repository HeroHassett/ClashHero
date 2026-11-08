"""Formatting helpers shared by embeds."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime

import discord

from clashhero.constants import DEFAULT_EMBED_COLOR, ROLE_NAMES


def stars(n: int, total: int = 3) -> str:
    return "★" * n + "☆" * (total - n)


def role_name(role: str | None) -> str:
    return ROLE_NAMES.get(role or "", role or "—")


def signed(n: int) -> str:
    return f"{n:+d}"


def ts(dt: datetime | str | None, style: str = "R") -> str:
    """Discord timestamp markup (renders in each viewer's timezone)."""
    if dt is None:
        return "—"
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return f"<t:{int(dt.timestamp())}:{style}>"


def player_link(tag: str) -> str:
    return f"https://link.clashofclans.com/?action=OpenPlayerProfile&tag={tag.lstrip('#')}"


def clan_link(tag: str) -> str:
    return f"https://link.clashofclans.com/?action=OpenClanProfile&tag={tag.lstrip('#')}"


_ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufe0e", "\ufe0f"}


def display_width(text: str) -> int:
    """Columns ``text`` takes in a monospace font: wide East Asian characters and most emoji
    take 2, combining marks and zero-width characters take 0."""
    width = 0
    for ch in text:
        if ch in _ZERO_WIDTH or unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad(text: str, width: int, *, right: bool = False) -> str:
    """ljust/rjust by display width instead of character count."""
    fill = " " * max(0, width - display_width(text))
    return fill + text if right else text + fill


def table(rows: Sequence[Sequence[object]], headers: Sequence[str] | None = None) -> str:
    """Monospace table in a code block, column widths fitted to content (by display width, so
    names in Chinese, Japanese, Korean or with emoji stay aligned)."""
    data = [list(map(str, r)) for r in rows]
    if headers:
        data.insert(0, list(headers))
    if not data:
        return "```\n(no data)\n```"
    widths = [max(display_width(r[i]) for r in data if i < len(r)) for i in range(len(data[0]))]
    lines = [" ".join(pad(c, widths[i]) for i, c in enumerate(r)).rstrip() for r in data]
    if headers:
        lines.insert(1, " ".join("-" * w for w in widths))
    return "```\n" + "\n".join(lines) + "\n```"


def chunk_lines(lines: Sequence[str], limit: int = 4000) -> list[str]:
    """Split lines into strings under ``limit`` chars (embed description cap is 4096)."""
    out, cur = [], ""
    for line in lines:
        if len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        out.append(cur)
    return out or [""]


async def guild_color(client, guild_id: int | None) -> discord.Color:
    if guild_id is None:
        return discord.Color(DEFAULT_EMBED_COLOR)
    s = await client.db.get_guild_settings(guild_id)
    return discord.Color(s["embed_color"] or DEFAULT_EMBED_COLOR)


def maintenance_notice(client) -> str | None:
    """One-line notice while the Clash API is down for maintenance, else None."""
    collector = getattr(client, "collector", None)
    if not getattr(collector, "in_maintenance", False):
        return None
    since = collector.maintenance_since
    when = f" since {since:%H:%M} UTC" if since else ""
    return f"🛠️ Clash of Clans API maintenance{when}: data is paused and may be out of date"


async def make_embed(client, guild_id: int | None, **kwargs) -> discord.Embed:
    kwargs.setdefault("color", await guild_color(client, guild_id))
    embed = discord.Embed(**kwargs)
    notice = maintenance_notice(client)
    if notice:
        embed.set_author(name=notice)
    return embed


def duration(seconds: float) -> str:
    """'1h 12m', '45m', '3d 2h'."""
    minutes = max(0, int(seconds // 60))
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    parts = [f"{days}d"] if days else []
    if hours:
        parts.append(f"{hours}h")
    if mins or not parts:
        parts.append(f"{mins}m")
    return " ".join(parts[:2])
