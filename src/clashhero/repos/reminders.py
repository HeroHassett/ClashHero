"""Reminder storage and the DB queries that find who still has attacks to use."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from clashhero.constants import ROLE_NAMES, ROLE_RANK
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
RAID_BASE_ATTACKS = 5
WAR_TYPES = ("random", "friendly", "cwl")

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
    """Someone who still has something to do. ``detail`` e.g. "1/2" or "1200/4000"; ``done`` and
    ``total`` are the same numbers. ``role`` is the API clan role (member/admin/coLeader/leader)
    in the event's clan, or None when unknown."""

    player_tag: str
    name: str
    detail: str
    town_hall: int | None = None
    role: str | None = None
    done: int = 0
    total: int = 0

    @property
    def left(self) -> int:
        return max(0, self.total - self.done)


# ------------------------------------------------------------------ target filters

_ROLE_WORDS = {
    "member": "member",
    "members": "member",
    "elder": "admin",
    "elders": "admin",
    "admin": "admin",
    "coleader": "coLeader",
    "coleaders": "coLeader",
    "co": "coLeader",
    "leader": "leader",
}
_ANY = {"any", "all", "everyone", "*"}


def parse_roles(raw: str) -> list[str] | None:
    """Parse clan roles like ``elder, co-leader`` into API names (lowest rank first), e.g.
    ``["admin", "coLeader"]``. ``any``/``all`` give None (no filter). Raises ValueError on an
    unknown role."""
    text = re.sub(r"co[\s_-]*leader", "coleader", raw.strip().lower())
    words = [w for w in re.split(r"[\s,;/|+]+", text) if w]
    if not words or all(w in _ANY for w in words):
        return None
    out = set()
    for w in words:
        if w not in _ROLE_WORDS:
            raise ValueError(f"unknown role {w!r}")
        out.add(_ROLE_WORDS[w])
    return sorted(out, key=ROLE_RANK.__getitem__)


def _get(row: Mapping[str, Any], key: str) -> Any:
    return row[key] if key in row.keys() else None


@dataclass(frozen=True)
class Filters:
    """Optional limits on who a reminder pings. ``None`` everywhere = everyone (the default)."""

    min_th: int | None = None
    max_th: int | None = None
    clan_roles: frozenset[str] | None = None
    war_type: str | None = None  # war reminders: random | friendly | cwl (None: random + friendly)
    games_below: int | None = None  # Clan Games: only members below this many points
    raid_min_left: int | None = None  # raids: only members with at least this many attacks left

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Filters:
        roles = _get(row, "clan_roles")
        return cls(
            min_th=_get(row, "min_th"),
            max_th=_get(row, "max_th"),
            clan_roles=frozenset(roles.split()) if roles else None,
            war_type=_get(row, "war_type") or None,
            games_below=_get(row, "games_below"),
            raid_min_left=_get(row, "raid_min_left"),
        )

    def columns(self) -> dict[str, Any]:
        """Column values for the reminders table."""
        roles = sorted(self.clan_roles, key=lambda r: ROLE_RANK.get(r, 0)) if self.clan_roles else None
        return {
            "min_th": self.min_th,
            "max_th": self.max_th,
            "clan_roles": " ".join(roles) if roles else None,
            "war_type": self.war_type,
            "games_below": self.games_below,
            "raid_min_left": self.raid_min_left,
        }

    def allows(self, p: Pending, type_: str) -> bool:
        """Whether ``p`` passes every set filter. Unknown TH/role never passes a TH/role filter."""
        if self.min_th is not None and (p.town_hall is None or p.town_hall < self.min_th):
            return False
        if self.max_th is not None and (p.town_hall is None or p.town_hall > self.max_th):
            return False
        if self.clan_roles and p.role not in self.clan_roles:
            return False
        if type_ == "games" and self.games_below is not None and p.done >= self.games_below:
            return False
        return not (type_ == "raid" and self.raid_min_left is not None and p.left < self.raid_min_left)

    def apply(self, pending: Iterable[Pending], type_: str) -> list[Pending]:
        return [p for p in pending if self.allows(p, type_)]

    def describe(self) -> str:
        """Short human summary, or "" when no filter is set."""
        parts = []
        if self.min_th is not None and self.max_th is not None:
            parts.append(
                f"TH{self.min_th}" if self.min_th == self.max_th else f"TH{self.min_th}-{self.max_th}"
            )
        elif self.min_th is not None:
            parts.append(f"TH{self.min_th}+")
        elif self.max_th is not None:
            parts.append(f"TH{self.max_th} and below")
        if self.clan_roles:
            roles = sorted(self.clan_roles, key=lambda r: ROLE_RANK.get(r, 0))
            parts.append(", ".join(ROLE_NAMES.get(r, r) for r in roles))
        if self.war_type:
            parts.append(
                {"random": "random wars", "friendly": "friendly wars", "cwl": "CWL wars"}.get(
                    self.war_type, self.war_type
                )
            )
        if self.games_below is not None:
            parts.append(f"below {self.games_below:,} pts")
        if self.raid_min_left is not None:
            parts.append(f"{self.raid_min_left}+ raid attacks left")
        return " · ".join(parts)


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
    filters: Filters | None = None,
) -> int:
    cols = (filters or Filters()).columns()
    names = ", ".join(cols)
    return await db.execute(
        "INSERT INTO reminders (guild_id, type, clans, duration_seconds, message, channel_id, "
        f"exclude_participant_list, created_by, {names}) "
        f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, {', '.join('?' * len(cols))})",
        (
            guild_id,
            type_,
            " ".join(clans),
            duration_seconds,
            message,
            channel_id,
            int(exclude_participant_list),
            created_by,
            *cols.values(),
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


class _Clear:
    def __repr__(self) -> str:
        return "CLEAR"


CLEAR = _Clear()
"""Pass as a field value to :func:`update_reminder` to reset that column to NULL."""

_UPDATABLE = {
    "duration_seconds",
    "message",
    "channel_id",
    "clans",
    "exclude_participant_list",
    *Filters().columns(),
}


async def update_reminder(db: Database, guild_id: int, reminder_id: int, **fields) -> bool:
    """Set the given columns; ``None`` values are skipped and :data:`CLEAR` sets NULL."""
    fields = {k: (None if v is CLEAR else v) for k, v in fields.items() if k in _UPDATABLE and v is not None}
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


async def active_war(db: Database, clan_tag: str, cwl: bool, war_type: str | None = None) -> Row | None:
    """The clan's most recent war in battle day: CWL rounds when ``cwl``, else regular and friendly
    wars. ``war_type`` (random | friendly | cwl) narrows it to exactly that kind instead."""
    if war_type:
        cond, params = "war_type = ?", (clan_tag, war_type)
    else:
        cond, params = f"war_type {'=' if cwl else '!='} 'cwl'", (clan_tag,)
    return await db.fetchone(
        f"SELECT * FROM wars WHERE clan_tag = ? AND state = 'inWar' AND {cond} "
        "ORDER BY preparation_start DESC LIMIT 1",
        params,
    )


async def war_pending(db: Database, war: Row) -> list[Pending]:
    per = war["attacks_per_member"] or 1
    rows = await db.fetchall(
        "SELECT m.player_tag, m.name, m.map_position, COALESCE(m.town_hall, p.town_hall) AS th, "
        "CASE WHEN p.clan_tag = ? THEN p.clan_role END AS role, "
        "(SELECT COUNT(*) FROM war_attacks a WHERE a.war_id = m.war_id "
        " AND a.attacker_tag = m.player_tag) AS used "
        "FROM war_members m LEFT JOIN players p ON p.player_tag = m.player_tag "
        "WHERE m.war_id = ? AND m.is_opponent = 0 ORDER BY m.map_position",
        (war["clan_tag"], war["id"]),
    )
    return [
        Pending(r["player_tag"], r["name"], f"{r['used']}/{per}", r["th"], r["role"], r["used"], per)
        for r in rows
        if r["used"] < per
    ]


async def active_raid(db: Database, clan_tag: str) -> Row | None:
    return await db.fetchone(
        "SELECT * FROM raid_seasons WHERE clan_tag = ? AND state = 'ongoing' "
        "ORDER BY start_time DESC LIMIT 1",
        (clan_tag,),
    )


async def raid_pending(db: Database, clan_tag: str, start_time: str) -> list[Pending]:
    """Raiders with attacks left, plus current members who haven't raided at all."""
    raided = await db.fetchall(
        "SELECT r.*, p.town_hall, CASE WHEN p.clan_tag = r.clan_tag THEN p.clan_role END AS role "
        "FROM raid_members r LEFT JOIN players p ON p.player_tag = r.player_tag "
        "WHERE r.clan_tag = ? AND r.start_time = ?",
        (clan_tag, start_time),
    )
    out = [
        Pending(
            r["player_tag"],
            r["name"],
            f"{r['attacks']}/{r['attack_limit']}",
            r["town_hall"],
            r["role"],
            r["attacks"],
            r["attack_limit"],
        )
        for r in raided
        if r["attacks"] < r["attack_limit"]
    ]
    seen = {r["player_tag"] for r in raided}
    for p in await db.clan_member_rows(clan_tag):
        if p["player_tag"] not in seen:
            out.append(
                Pending(
                    p["player_tag"],
                    p["name"],
                    f"0/{RAID_BASE_ATTACKS}",
                    p["town_hall"],
                    p["clan_role"],
                    0,
                    RAID_BASE_ATTACKS,
                )
            )
    return out


async def games_pending(db: Database, clan_tag: str, games_id: str) -> list[Pending]:
    points = {
        r["player_tag"]: r["points"]
        for r in await db.fetchall(
            "SELECT player_tag, points FROM clan_games_points WHERE games_id = ?", (games_id,)
        )
    }
    out = []
    for p in await db.clan_member_rows(clan_tag):
        pts = points.get(p["player_tag"], 0)
        if pts < CLAN_GAMES_TARGET:
            out.append(
                Pending(
                    p["player_tag"],
                    p["name"],
                    f"{pts}/{CLAN_GAMES_TARGET}",
                    p["town_hall"],
                    p["clan_role"],
                    pts,
                    CLAN_GAMES_TARGET,
                )
            )
    return out
