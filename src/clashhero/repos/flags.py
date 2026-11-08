"""Queries for player flags (bans / strikes)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from clashhero.db.database import Database, Row

FLAG_TYPES = ("ban", "strike")


async def create_flag(
    db: Database,
    guild_id: int,
    player_tag: str,
    flag_type: str,
    reason: str | None,
    created_by: int,
    now: datetime,
    expiry_days: int | None = None,
) -> int:
    if flag_type not in FLAG_TYPES:
        raise ValueError(flag_type)
    expires = (now + timedelta(days=expiry_days)).isoformat() if expiry_days else None
    return await db.execute(
        "INSERT INTO flags (guild_id, player_tag, flag_type, reason, created_by, created_at, "
        "expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (guild_id, player_tag, flag_type, reason, created_by, now.isoformat(), expires),
    )


async def delete_flag(db: Database, guild_id: int, flag_id: int) -> bool:
    return (
        await db.execute_rowcount("DELETE FROM flags WHERE guild_id = ? AND id = ?", (guild_id, flag_id)) > 0
    )


async def list_flags(
    db: Database,
    guild_id: int,
    now: datetime,
    *,
    flag_type: str | None = None,
    player_tag: str | None = None,
    clan_tags: Iterable[str] | None = None,
    include_expired: bool = False,
) -> list[Row]:
    sql = (
        "SELECT f.*, p.name, p.clan_tag FROM flags f "
        "LEFT JOIN players p ON p.player_tag = f.player_tag WHERE f.guild_id = ?"
    )
    params: list = [guild_id]
    if not include_expired:
        sql += " AND (f.expires_at IS NULL OR f.expires_at > ?)"
        params.append(now.isoformat())
    if flag_type:
        sql += " AND f.flag_type = ?"
        params.append(flag_type)
    if player_tag:
        sql += " AND f.player_tag = ?"
        params.append(player_tag)
    if clan_tags is not None:
        tags = list(clan_tags)
        sql += f" AND p.clan_tag IN ({','.join('?' * len(tags)) or 'NULL'})"
        params.extend(tags)
    return await db.fetchall(sql + " ORDER BY f.created_at DESC", params)


async def active_flags(db: Database, guild_id: int, player_tag: str, now: datetime) -> list[Row]:
    return await list_flags(db, guild_id, now, player_tag=player_tag)
