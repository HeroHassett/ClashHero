"""War base layouts: registry, posts, and CWL usage tracking."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from clashhero.db.database import Database, Row

BASE_TYPES = ("war", "cwl", "legend", "farming", "hybrid", "fun")
_EDITABLE = frozenset({"name", "town_hall", "type", "link", "image_url", "notes", "channel_id", "message_id"})


def filter_bases(rows: Iterable[Mapping[str, Any]], current: str, limit: int = 25) -> list[Mapping[str, Any]]:
    """Autocomplete filter: case-insensitive substring on name, then 'TH<n>' prefix match."""
    cur = current.strip().lower()
    out = []
    for r in rows:
        hay = f"{r['name']} th{r['town_hall']} {r['type']}".lower()
        if not cur or cur in hay:
            out.append(r)
    out.sort(key=lambda r: (not r["name"].lower().startswith(cur), -r["town_hall"], r["name"].lower()))
    return out[:limit]


def is_copy_link(link: str | None) -> bool:
    return bool(link) and link.startswith(("https://", "http://"))


async def add_base(
    db: Database,
    guild_id: int,
    name: str,
    town_hall: int,
    *,
    added_by: int,
    type: str = "war",
    link: str | None = None,
    image_url: str | None = None,
    notes: str | None = None,
) -> int:
    return await db.execute(
        "INSERT INTO war_bases (guild_id, name, town_hall, type, link, image_url, notes, added_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (guild_id, name, town_hall, type, link, image_url, notes, added_by),
    )


async def get_base(db: Database, base_id: int) -> Row | None:
    return await db.fetchone("SELECT * FROM war_bases WHERE id = ?", (base_id,))


async def get_base_by_name(db: Database, guild_id: int, name: str) -> Row | None:
    return await db.fetchone(
        "SELECT * FROM war_bases WHERE guild_id = ? AND name = ? COLLATE NOCASE", (guild_id, name)
    )


async def all_bases(db: Database, guild_id: int) -> list[Row]:
    return await db.fetchall(
        "SELECT * FROM war_bases WHERE guild_id = ? ORDER BY town_hall DESC, name", (guild_id,)
    )


async def update_base(db: Database, base_id: int, **fields: Any) -> None:
    bad = set(fields) - _EDITABLE
    if bad:
        raise ValueError(f"Unknown base fields: {sorted(bad)}")
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        await db.execute(f"UPDATE war_bases SET {sets} WHERE id = ?", (*fields.values(), base_id))


async def delete_base(db: Database, base_id: int) -> None:
    await db.execute("DELETE FROM war_bases WHERE id = ?", (base_id,))


async def list_bases_with_usage(
    db: Database,
    guild_id: int,
    season_id: str,
    *,
    town_hall: int | None = None,
    type: str | None = None,
    unused_only: bool = False,
) -> list[Row]:
    """Bases plus their use count in ``season_id``; least-used first within each TH."""
    sql = (
        "SELECT b.*, COUNT(u.id) AS uses FROM war_bases b "
        "LEFT JOIN base_usages u ON u.base_id = b.id AND u.season_id = ? WHERE b.guild_id = ?"
    )
    params: list[Any] = [season_id, guild_id]
    if town_hall is not None:
        sql += " AND b.town_hall = ?"
        params.append(town_hall)
    if type is not None:
        sql += " AND b.type = ?"
        params.append(type)
    sql += " GROUP BY b.id"
    if unused_only:
        sql += " HAVING uses = 0"
    return await db.fetchall(sql + " ORDER BY b.town_hall DESC, uses, b.name", params)


async def record_usage(
    db: Database, base_id: int, season_id: str, war_round: int | None, player_tag: str | None
) -> int:
    return await db.execute(
        "INSERT INTO base_usages (base_id, season_id, war_round, player_tag) VALUES (?, ?, ?, ?)",
        (base_id, season_id, war_round, player_tag),
    )


async def remove_usage(db: Database, base_id: int, season_id: str, war_round: int | None = None) -> bool:
    """Undo the most recent usage of a base this season (optionally for a specific round)."""
    sql = "SELECT id FROM base_usages WHERE base_id = ? AND season_id = ?"
    params: list[Any] = [base_id, season_id]
    if war_round is not None:
        sql += " AND war_round = ?"
        params.append(war_round)
    usage_id = await db.fetchval(sql + " ORDER BY used_at DESC, id DESC LIMIT 1", params)
    if usage_id is None:
        return False
    await db.execute("DELETE FROM base_usages WHERE id = ?", (usage_id,))
    return True


async def usage_report(db: Database, guild_id: int, season_id: str) -> list[Row]:
    """Every usage this season with base and player names, ordered by round."""
    return await db.fetchall(
        "SELECT u.*, b.name AS base_name, b.town_hall, p.name AS player_name FROM base_usages u "
        "JOIN war_bases b ON b.id = u.base_id LEFT JOIN players p ON p.player_tag = u.player_tag "
        "WHERE b.guild_id = ? AND u.season_id = ? "
        "ORDER BY u.war_round IS NULL, u.war_round, b.town_hall DESC, b.name",
        (guild_id, season_id),
    )


async def get_voting(db: Database, guild_id: int) -> bool:
    return bool(await db.fetchval("SELECT voting FROM base_settings WHERE guild_id = ?", (guild_id,), 1))


async def set_voting(db: Database, guild_id: int, enabled: bool) -> None:
    await db.execute(
        "INSERT INTO base_settings (guild_id, voting) VALUES (?, ?) "
        "ON CONFLICT (guild_id) DO UPDATE SET voting = excluded.voting",
        (guild_id, int(enabled)),
    )
