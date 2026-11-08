"""Formatting helpers shared by embeds."""

from __future__ import annotations

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


def table(rows: Sequence[Sequence[object]], headers: Sequence[str] | None = None) -> str:
    """Monospace table in a code block, column widths fitted to content."""
    data = [list(map(str, r)) for r in rows]
    if headers:
        data.insert(0, list(headers))
    if not data:
        return "```\n(no data)\n```"
    widths = [max(len(r[i]) for r in data if i < len(r)) for i in range(len(data[0]))]
    lines = [" ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip() for r in data]
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


async def make_embed(client, guild_id: int | None, **kwargs) -> discord.Embed:
    kwargs.setdefault("color", await guild_color(client, guild_id))
    return discord.Embed(**kwargs)
