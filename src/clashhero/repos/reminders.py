"""Reminder storage and the DB queries that find who still has attacks to use."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from clashhero.db import Database
from clashhero.db.database import Row

REMINDER_TYPES = ("war", "cwl", "raid", "games")
MIN_DURATION = timedelta(minutes=15)
MAX_DURATION = {
    "war": timedelta(hours=24),
    "cwl": timedelta(hours=24),
    "raid": timedelta(days=3),
    "games": timedelta(days=6),
}
CLAN_GAMES_TARGET = 4000

_DURATION_RE = re.compile(r"(\d+)\s*([dhm])", re.I)


def parse_duration(raw: str) -> timedelta:
    """Parse "1d", "2h30m", "45m", "1h 15m". Raises ValueError on anything else."""
    text = raw.strip().lower().replace(" ", "")
    if not text:
        raise ValueError("empty duration")
    pos, total = 0, timedelta()
    for m in _DURATION_RE.finditer(text):
        if m.start() != pos:
            raise ValueError(f"bad duration {raw!r}")
        n, unit = int(m.group(1)), m.group(2)
        total += {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}[unit]
        pos = m.end()
    if pos != len(text):
        raise ValueError(f"bad duration {raw!r}")
    return total


def format_duration(seconds: int) -> str:
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = [f"{d}d" if d else "", f"{h}h" if h else "", f"{m}m" if m else ""]
    return "".join(parts) or "0m"


def is_due(now: datetime, end: datetime, duration_seconds: int, already_sent: bool) -> bool:
    """A reminder fires once, between (end - duration) and end."""
    if already_sent:
        return False
    return end - timedelta(seconds=duration_seconds) <= now < end


@dataclass
class Pending:
    """Someone who still has something to do. ``detail`` e.g. "1/2" or "1200/4000"."""

    player_tag: str
    name: str
    detail: str


# ------------------------------------------------------------------ CRUD


async def create_reminder(
    db: Database,
    guild_id: int,
    type_: str,
    clans: list[str],
    duration_seconds: int,
    message: str,
    channel_id: int,
    exclude_participant_list: bool,
    created_by: int,
) -> int:
    return await db.execute(
        "INSERT INTO reminders (guild_id, type, clans, duration_seconds, message, channel_id, "
        "exclude_participant_list, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            guild_id,
            type_,
            " ".join(clans),
            duration_seconds,
            message,
            channel_id,
            int(exclude_participant_list),
            created_by,
        ),
    )


async def list_reminders(db: Database, guild_id: int, type_: str | None = None) -> list[Row]:
    sql, params = "SELECT * FROM reminders WHERE guild_id = ?", [guild_id]
    if type_:
        sql += " AND type = ?"
        params.append(type_)
    return await db.fetchall(sql + " ORDER BY type, duration_seconds DESC", params)


async def get_reminder(db: Database, guild_id: int, reminder_id: int) -> Row | None:
    return await db.fetchone("SELECT * FROM reminders WHERE guild_id = ? AND id = ?", (guild_id, reminder_id))


async def update_reminder(db: Database, guild_id: int, reminder_id: int, **fields) -> bool:
    allowed = {"duration_seconds", "message", "channel_id", "clans", "exclude_participant_list"}
    fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not fields:
        return False
    sets = ", ".join(f"{k} = ?" for k in fields)
    n = await db.execute_rowcount(
        f"UPDATE reminders SET {sets} WHERE guild_id = ? AND id = ?",
        (*fields.values(), guild_id, reminder_id),
    )
    return n > 0


async def delete_reminder(db: Database, guild_id: int, reminder_id: int) -> bool:
    return (
        await db.execute_rowcount(
            "DELETE FROM reminders WHERE guild_id = ? AND id = ?", (guild_id, reminder_id)
        )
        > 0
    )


async def all_reminders(db: Database) -> list[Row]:
    return await db.fetchall("SELECT * FROM reminders")


async def was_sent(db: Database, reminder_id: int, event_key: str) -> bool:
    return bool(
        await db.fetchval(
            "SELECT 1 FROM reminder_sent WHERE reminder_id = ? AND event_key = ?", (reminder_id, event_key)
        )
    )


async def mark_sent(db: Database, reminder_id: int, event_key: str) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO reminder_sent (reminder_id, event_key) VALUES (?, ?)",
        (reminder_id, event_key),
    )


async def set_config(db: Database, guild_id: int, type_: str, role_id: int | None, mode: str) -> None:
    if role_id is None:
        await db.execute("DELETE FROM reminder_config WHERE guild_id = ? AND type = ?", (guild_id, type_))
        return
    await db.execute(
        "INSERT INTO reminder_config (guild_id, type, role_id, mode) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (guild_id, type) DO UPDATE SET role_id = excluded.role_id, mode = excluded.mode",
        (guild_id, type_, role_id, mode),
    )


async def get_config(db: Database, guild_id: int, type_: str) -> Row | None:
    return await db.fetchone(
        "SELECT * FROM reminder_config WHERE guild_id = ? AND type = ?", (guild_id, type_)
    )


# ------------------------------------------------------------------ active events


async def active_war(db: Database, clan_tag: str, cwl: bool) -> Row | None:
    """The clan's most recent war in battle day, filtered by CWL vs regular."""
    op = "=" if cwl else "!="
    return await db.fetchone(
        f"SELECT * FROM wars WHERE clan_tag = ? AND state = 'inWar' AND war_type {op} 'cwl' "
        "ORDER BY preparation_start DESC LIMIT 1",
        (clan_tag,),
    )


async def war_pending(db: Database, war: Row) -> list[Pending]:
    per = war["attacks_per_member"] or 1
    rows = await db.fetchall(
        "SELECT m.player_tag, m.name, m.map_position, "
        "(SELECT COUNT(*) FROM war_attacks a WHERE a.war_id = m.war_id "
        " AND a.attacker_tag = m.player_tag) AS used "
        "FROM war_members m WHERE m.war_id = ? AND m.is_opponent = 0 ORDER BY m.map_position",
        (war["id"],),
    )
    return [Pending(r["player_tag"], r["name"], f"{r['used']}/{per}") for r in rows if r["used"] < per]


async def active_raid(db: Database, clan_tag: str) -> Row | None:
    return await db.fetchone(
        "SELECT * FROM raid_seasons WHERE clan_tag = ? AND state = 'ongoing' "
        "ORDER BY start_time DESC LIMIT 1",
        (clan_tag,),
    )


async def raid_pending(db: Database, clan_tag: str, start_time: str) -> list[Pending]:
    """Raiders with attacks left, plus current members who haven't raided at all."""
    raided = await db.fetchall(
        "SELECT * FROM raid_members WHERE clan_tag = ? AND start_time = ?", (clan_tag, start_time)
    )
    out = [
        Pending(r["player_tag"], r["name"], f"{r['attacks']}/{r['attack_limit']}")
        for r in raided
        if r["attacks"] < r["attack_limit"]
    ]
    seen = {r["player_tag"] for r in raided}
    for p in await db.clan_member_rows(clan_tag):
        if p["player_tag"] not in seen:
            out.append(Pending(p["player_tag"], p["name"], "0/5"))
    return out


async def games_pending(db: Database, clan_tag: str, games_id: str) -> list[Pending]:
    points = {
        r["player_tag"]: r["points"]
        for r in await db.fetchall(
            "SELECT player_tag, points FROM clan_games_points WHERE games_id = ?", (games_id,)
        )
    }
    return [
        Pending(p["player_tag"], p["name"], f"{points.get(p['player_tag'], 0)}/{CLAN_GAMES_TARGET}")
        for p in await db.clan_member_rows(clan_tag)
        if points.get(p["player_tag"], 0) < CLAN_GAMES_TARGET
    ]
