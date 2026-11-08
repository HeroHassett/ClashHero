"""Scheduled boards: storage plus the pure schedule maths (unit-tested).

A board is either *fixed* (posted once per slot: daily, weekly, at season end or at the legend-day
reset) or *live* (one message edited every N minutes). ``last_run`` stores the slot that was last
posted for fixed boards and the time of the last edit for live ones; :func:`claim_run` updates it
with a compare-and-swap so a slot is never posted twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

from clashhero.db.database import Database, Row
from clashhero.utils.seasons import SEASON_LENGTH, WEEK, game_season, legend_day_bounds

BOARD_TYPES: dict[str, str] = {
    "donations": "Season donations",
    "trophies": "Trophies",
    "legends": "Ranked / legend leaderboard",
    "war_stats": "War hit rates (season)",
    "war_results": "War results (season)",
    "clan_games": "Clan Games",
    "capital": "Capital contributions (season)",
    "activity": "Most active (season)",
}

SCHEDULES: dict[str, str] = {
    "daily": "Daily at a time (UTC)",
    "weekly": "Weekly on a weekday at a time (UTC)",
    "season_end": "At season end",
    "legend_reset": "At the legend-day reset (05:00 UTC)",
    "live": "Live (one message edited every N minutes)",
}
FIXED_SCHEDULES = ("daily", "weekly", "season_end", "legend_reset")

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

MIN_LIVE_MINUTES = 10
MAX_LIVE_MINUTES = 1440
DEFAULT_LIVE_MINUTES = 30
MAX_BOARDS_PER_GUILD = 25

# Season-end and reset boards wait a few minutes so the final polls of the period land first.
SETTLE = timedelta(minutes=5)
# A fixed slot missed (e.g. bot offline) is still posted if we're back within this window.
GRACE: dict[str, timedelta] = {
    "daily": timedelta(hours=2),
    "weekly": timedelta(hours=2),
    "legend_reset": timedelta(hours=2),
    "season_end": timedelta(hours=24),
}
# The loop ticks once a minute; tolerate drift so a 10-minute live board isn't edited every 11.
LIVE_SLACK = timedelta(seconds=30)

_PERIOD = {
    "daily": timedelta(days=1),
    "weekly": WEEK,
    "season_end": SEASON_LENGTH,
    "legend_reset": timedelta(days=1),
}


# ------------------------------------------------------------------ pure schedule logic


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def to_iso(dt: datetime) -> str:
    return _utc(dt).isoformat(timespec="seconds")


def from_iso(raw: str | None) -> datetime | None:
    return _utc(datetime.fromisoformat(raw)) if raw else None


def parse_hhmm(raw: str) -> tuple[int, int]:
    """ "18:30" / "9:05" / "0930" -> (hour, minute). Raises ValueError otherwise."""
    text = raw.strip().lower().removesuffix("utc").strip()
    if ":" in text:
        h, _, m = text.partition(":")
    elif text.isdigit() and len(text) in (3, 4):
        h, m = text[:-2], text[-2:]
    elif text.isdigit() and len(text) <= 2:
        h, m = text, "0"
    else:
        raise ValueError(f"bad time {raw!r}")
    if not (h.strip().isdigit() and m.strip().isdigit()):
        raise ValueError(f"bad time {raw!r}")
    hour, minute = int(h), int(m)
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"bad time {raw!r}")
    return hour, minute


@dataclass(frozen=True)
class Schedule:
    kind: str
    hour: int | None = None
    minute: int | None = None
    weekday: int | None = None
    interval_minutes: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in SCHEDULES:
            raise ValueError(f"unknown schedule {self.kind!r}")
        if self.kind in ("daily", "weekly") and (self.hour is None or self.minute is None):
            raise ValueError("daily and weekly schedules need a time")
        if self.kind == "weekly" and self.weekday not in range(7):
            raise ValueError("weekly schedules need a weekday (0 = Monday)")
        if self.kind == "live" and not (
            self.interval_minutes is not None
            and MIN_LIVE_MINUTES <= self.interval_minutes <= MAX_LIVE_MINUTES
        ):
            raise ValueError(f"live boards refresh every {MIN_LIVE_MINUTES}-{MAX_LIVE_MINUTES} minutes")

    @classmethod
    def from_row(cls, row: Any) -> Schedule:
        return cls(row["schedule"], row["hour"], row["minute"], row["weekday"], row["interval_min"])

    @property
    def is_live(self) -> bool:
        return self.kind == "live"

    @property
    def settle(self) -> timedelta:
        return SETTLE if self.kind in ("season_end", "legend_reset") else timedelta(0)

    def describe(self) -> str:
        if self.kind == "daily":
            return f"Daily at {self.hour:02d}:{self.minute:02d} UTC"
        if self.kind == "weekly":
            return f"{WEEKDAYS[self.weekday]}s at {self.hour:02d}:{self.minute:02d} UTC"  # type: ignore[index]
        if self.kind == "season_end":
            return "At season end"
        if self.kind == "legend_reset":
            return "At the legend-day reset (05:00 UTC)"
        return f"Live, every {self.interval_minutes} min"


def previous_slot(s: Schedule, now: datetime, anchor: datetime | None = None) -> datetime:
    """The latest scheduled moment at or before ``now`` (fixed schedules only)."""
    now = _utc(now)
    if s.kind == "daily":
        slot = datetime.combine(now.date(), time(s.hour, s.minute, tzinfo=UTC))  # type: ignore[arg-type]
        return slot if slot <= now else slot - timedelta(days=1)
    if s.kind == "weekly":
        day = now.date() - timedelta(days=(now.weekday() - s.weekday) % 7)  # type: ignore[operator]
        slot = datetime.combine(day, time(s.hour, s.minute, tzinfo=UTC))  # type: ignore[arg-type]
        return slot if slot <= now else slot - WEEK
    if s.kind == "season_end":
        return game_season(now, anchor).start
    if s.kind == "legend_reset":
        return legend_day_bounds(now)[0]
    raise ValueError(f"{s.kind} has no fixed slots")


def next_slot(s: Schedule, now: datetime, anchor: datetime | None = None) -> datetime:
    """The earliest scheduled moment strictly after ``now`` (fixed schedules only)."""
    return previous_slot(s, now, anchor) + _PERIOD[s.kind]


def post_time(s: Schedule, slot: datetime) -> datetime:
    """When a slot is actually posted (season-end/reset boards wait for the final polls)."""
    return slot + s.settle


def initial_last_run(s: Schedule, now: datetime, anchor: datetime | None = None) -> datetime | None:
    """``last_run`` for a new board: every slot whose post time has passed counts as done, so the
    first scheduled post is the next one. Live boards start empty (posted straight away)."""
    if s.is_live:
        return None
    return previous_slot(s, _utc(now) - s.settle, anchor)


def due_slot(
    s: Schedule, now: datetime, last_run: datetime | None, anchor: datetime | None = None
) -> datetime | None:
    """The slot to post right now, or None. Store the returned value as the new ``last_run``."""
    now = _utc(now)
    if s.is_live:
        interval = timedelta(minutes=s.interval_minutes or DEFAULT_LIVE_MINUTES)
        if last_run is None or now >= _utc(last_run) + interval - LIVE_SLACK:
            return now
        return None
    slot = previous_slot(s, now - s.settle, anchor)
    if last_run is not None and slot <= _utc(last_run):
        return None
    if now - post_time(s, slot) > GRACE[s.kind]:
        return None  # missed while offline; wait for the next slot
    return slot


def next_post_time(
    s: Schedule, now: datetime, last_run: datetime | None, anchor: datetime | None = None
) -> datetime:
    """When the board will next be posted / edited (for display)."""
    now = _utc(now)
    if s.is_live:
        if last_run is None:
            return now
        return max(now, _utc(last_run) + timedelta(minutes=s.interval_minutes or DEFAULT_LIVE_MINUTES))
    if due_slot(s, now, last_run, anchor) is not None:
        return now
    return post_time(s, next_slot(s, now - s.settle, anchor))


def reference_time(s: Schedule, slot: datetime | None, now: datetime) -> datetime:
    """The moment whose season/day a post describes. Season-end and reset posts describe the
    period that just ended; everything else the current one."""
    if slot is not None and s.kind in ("season_end", "legend_reset"):
        return _utc(slot) - timedelta(seconds=1)
    return _utc(now)


def board_clans(row: Any) -> list[str]:
    """Stored clan tags; empty means every clan linked to the server at post time."""
    return (row["clans"] or "").split()


# ------------------------------------------------------------------ storage


async def create_board(
    db: Database,
    guild_id: int,
    board_type: str,
    clans: list[str] | None,
    channel_id: int,
    schedule: Schedule,
    created_by: int,
    now: datetime,
) -> int:
    if board_type not in BOARD_TYPES:
        raise ValueError(board_type)
    last = initial_last_run(schedule, now)
    return await db.execute(
        "INSERT INTO autoboards (guild_id, board_type, clans, channel_id, schedule, hour, minute, "
        "weekday, interval_min, last_run, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            guild_id,
            board_type,
            " ".join(clans or []),
            channel_id,
            schedule.kind,
            schedule.hour,
            schedule.minute,
            schedule.weekday,
            schedule.interval_minutes,
            to_iso(last) if last else None,
            created_by,
            to_iso(now),
        ),
    )


async def get_board(db: Database, guild_id: int, board_id: int) -> Row | None:
    return await db.fetchone("SELECT * FROM autoboards WHERE guild_id = ? AND id = ?", (guild_id, board_id))


async def guild_boards(db: Database, guild_id: int) -> list[Row]:
    return await db.fetchall("SELECT * FROM autoboards WHERE guild_id = ? ORDER BY id", (guild_id,))


async def all_boards(db: Database) -> list[Row]:
    return await db.fetchall("SELECT * FROM autoboards ORDER BY id")


async def count_boards(db: Database, guild_id: int) -> int:
    return await db.fetchval("SELECT COUNT(*) FROM autoboards WHERE guild_id = ?", (guild_id,), 0)


async def delete_board(db: Database, guild_id: int, board_id: int) -> bool:
    n = await db.execute_rowcount(
        "DELETE FROM autoboards WHERE guild_id = ? AND id = ?", (guild_id, board_id)
    )
    return n > 0


async def delete_board_by_id(db: Database, board_id: int) -> None:
    await db.execute("DELETE FROM autoboards WHERE id = ?", (board_id,))


async def claim_run(db: Database, board_id: int, expected: str | None, new: datetime) -> bool:
    """Set ``last_run`` to ``new`` only if it still equals ``expected`` (the value read before
    deciding the board was due). False means someone else already handled this slot."""
    n = await db.execute_rowcount(
        "UPDATE autoboards SET last_run = ? WHERE id = ? AND last_run IS ?",
        (to_iso(new), board_id, expected),
    )
    return n > 0


async def set_message_id(db: Database, board_id: int, message_id: int | None) -> None:
    await db.execute("UPDATE autoboards SET message_id = ? WHERE id = ?", (message_id, board_id))
