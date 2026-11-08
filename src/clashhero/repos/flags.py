"""Queries for player flags (bans / strikes). Strikes carry a weight (1-10); a player's strike total
is the sum of the weights of their strikes."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from clashhero.db.database import Database, Row

FLAG_TYPES = ("ban", "strike")
MIN_WEIGHT, MAX_WEIGHT = 1, 10


def _in(values: list) -> str:
    return ",".join("?" * len(values)) or "NULL"


async def create_flag(
    db: Database,
    guild_id: int,
    player_tag: str,
    flag_type: str,
    reason: str | None,
    created_by: int,
    now: datetime,
    expiry_days: int | None = None,
    weight: int = 1,
) -> int:
    if flag_type not in FLAG_TYPES:
        raise ValueError(flag_type)
    if not MIN_WEIGHT <= weight <= MAX_WEIGHT:
        raise ValueError(f"weight must be {MIN_WEIGHT}-{MAX_WEIGHT}")
    if flag_type != "strike":
        weight = 1
    expires = (now + timedelta(days=expiry_days)).isoformat() if expiry_days else None
    return await db.execute(
        "INSERT INTO flags (guild_id, player_tag, flag_type, reason, created_by, created_at, "
        "expires_at, weight) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (guild_id, player_tag, flag_type, reason, created_by, now.isoformat(), expires, weight),
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
    min_strike_weight: int | None = None,
) -> list[Row]:
    """Flags, newest first. ``min_strike_weight`` keeps only players whose strike total (over the
    strikes this listing covers, i.e. expired ones too with ``include_expired``) is at least that."""
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
        sql += f" AND p.clan_tag IN ({_in(tags)})"
        params.extend(tags)
    if min_strike_weight:
        sub = "SELECT s.player_tag FROM flags s WHERE s.guild_id = ? AND s.flag_type = 'strike'"
        sub_params: list = [guild_id]
        if not include_expired:
            sub += " AND (s.expires_at IS NULL OR s.expires_at > ?)"
            sub_params.append(now.isoformat())
        sql += f" AND f.player_tag IN ({sub} GROUP BY s.player_tag HAVING SUM(s.weight) >= ?)"
        params += [*sub_params, min_strike_weight]
    return await db.fetchall(sql + " ORDER BY f.created_at DESC, f.id DESC", params)


async def active_flags(db: Database, guild_id: int, player_tag: str, now: datetime) -> list[Row]:
    return await list_flags(db, guild_id, now, player_tag=player_tag)


async def strike_totals(
    db: Database,
    guild_id: int,
    now: datetime,
    *,
    clan_tags: Iterable[str] | None = None,
    min_weight: int = 1,
) -> list[Row]:
    """Per player with active strikes: total weight, strike count, latest strike and whether they
    also have an active ban. Highest total first."""
    sql = (
        "SELECT f.player_tag, p.name, p.clan_tag, SUM(f.weight) AS total, COUNT(*) AS strikes, "
        "MAX(f.created_at) AS last_strike, "
        "EXISTS (SELECT 1 FROM flags b WHERE b.guild_id = f.guild_id AND b.player_tag = f.player_tag "
        "AND b.flag_type = 'ban' AND (b.expires_at IS NULL OR b.expires_at > ?)) AS banned "
        "FROM flags f LEFT JOIN players p ON p.player_tag = f.player_tag "
        "WHERE f.guild_id = ? AND f.flag_type = 'strike' AND (f.expires_at IS NULL OR f.expires_at > ?)"
    )
    params: list = [now.isoformat(), guild_id, now.isoformat()]
    if clan_tags is not None:
        tags = list(clan_tags)
        sql += f" AND p.clan_tag IN ({_in(tags)})"
        params.extend(tags)
    sql += (
        " GROUP BY f.player_tag HAVING total >= ? "
        "ORDER BY total DESC, strikes DESC, last_strike DESC, f.player_tag"
    )
    params.append(min_weight)
    return await db.fetchall(sql, params)


async def strike_total(db: Database, guild_id: int, player_tag: str, now: datetime) -> int:
    """A player's active strike total in a server."""
    return await db.fetchval(
        "SELECT COALESCE(SUM(weight), 0) FROM flags WHERE guild_id = ? AND player_tag = ? "
        "AND flag_type = 'strike' AND (expires_at IS NULL OR expires_at > ?)",
        (guild_id, player_tag, now.isoformat()),
        0,
    )
