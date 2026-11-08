"""Game-calendar boundaries. All times are UTC; functions are pure for testability.

Assumptions (adjust here if Supercell changes them):
- Seasons (Legend I tournaments, seasonal donations) are 28 days long and start on a Monday at
  05:00 UTC; one started 2026-11-02 05:00 UTC. Season ids are the start date, matching the API's
  ``legendStatistics`` season ids. ``SEASON_ANCHOR`` is any known season start; override it with
  the ``SEASON_ANCHOR`` env var (ISO-8601) if the cycle ever shifts.
- Ranked tiers below Legend I run weekly tournaments, Monday 05:00 UTC to Monday 05:00 UTC.
- A legend/ranked "day" resets at 05:00 UTC (8 attacks + 8 defenses per day in Legend I).
- CWL runs in the first ~10 days of each calendar month; its season id is "YYYY-MM".
- Raid Weekend runs Friday 07:00 UTC to Monday 07:00 UTC.
- Clan Games run from the 22nd 08:00 UTC to the 28th 08:00 UTC.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

RESET_TIME = time(5, 0, tzinfo=UTC)
SEASON_LENGTH = timedelta(days=28)
DEFAULT_SEASON_ANCHOR = datetime(2026, 11, 2, 5, tzinfo=UTC)
WEEK = timedelta(days=7)


def season_anchor() -> datetime:
    raw = os.getenv("SEASON_ANCHOR", "").strip()
    if not raw:
        return DEFAULT_SEASON_ANCHOR
    dt = datetime.fromisoformat(raw)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Window:
    id: str
    start: datetime
    end: datetime

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end


def game_season(now: datetime | None = None, anchor: datetime | None = None) -> Window:
    """The 28-day season containing ``now``. Its id is the ISO date the season starts."""
    now = now or utcnow()
    anchor = anchor or season_anchor()
    k = (now - anchor) // SEASON_LENGTH
    start = anchor + k * SEASON_LENGTH
    end = start + SEASON_LENGTH
    return Window(id=start.date().isoformat(), start=start, end=end)


def previous_game_season(now: datetime | None = None, anchor: datetime | None = None) -> Window:
    cur = game_season(now, anchor)
    return game_season(cur.start - timedelta(seconds=1), anchor)


def season_label(season: Window) -> str:
    """'Nov 2 – Nov 30, 2026' (the last day shown is the day the season resets)."""
    s, e = season.start, season.end
    year = f", {e.year}" if s.year == e.year else f", {s.year}–{e.year}"
    return f"{s:%b} {s.day} – {e:%b} {e.day}{year}"


def season_for_date(raw: str) -> Window | None:
    """The season containing a typed date ('2026-11-03', '2026-11'), or None if unparseable."""
    raw = raw.strip()
    try:
        day = datetime.fromisoformat(raw) if len(raw) > 7 else datetime.fromisoformat(f"{raw}-15")
    except ValueError:
        return None
    if day.tzinfo is None:
        day = day.replace(tzinfo=UTC)
    return game_season(day.replace(hour=12))


def season_by_id(season_id: str) -> Window:
    return season_for_date(season_id) or game_season()


# Ranked/legend tournaments share the 28-day cycle.
legend_season = game_season


def ranked_week(now: datetime | None = None) -> Window:
    """The weekly ranked tournament containing ``now`` (Monday 05:00 UTC, 7 days)."""
    now = now or utcnow()
    k = (now - season_anchor()) // WEEK
    start = season_anchor() + k * WEEK
    return Window(id=start.date().isoformat(), start=start, end=start + WEEK)


def legend_day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Start (inclusive) and end (exclusive) of the legend day containing ``now``."""
    now = now or utcnow()
    start = datetime.combine(now.date(), RESET_TIME)
    if now < start:
        start -= timedelta(days=1)
    return start, start + timedelta(days=1)


def legend_day_id(now: datetime | None = None) -> str:
    return legend_day_bounds(now)[0].date().isoformat()


def cwl_season_id(now: datetime | None = None) -> str:
    now = now or utcnow()
    return f"{now.year:04d}-{now.month:02d}"


def raid_weekend(now: datetime | None = None) -> Window:
    """The most recent raid weekend that started at or before ``now`` (may be over)."""
    now = now or utcnow()
    days_since_friday = (now.weekday() - 4) % 7
    start = datetime.combine((now - timedelta(days=days_since_friday)).date(), time(7, tzinfo=UTC))
    if start > now:
        start -= timedelta(days=7)
    return Window(id=start.date().isoformat(), start=start, end=start + timedelta(days=3))


def clan_games(now: datetime | None = None) -> Window:
    """This month's Clan Games window (may be in the future or past relative to ``now``)."""
    now = now or utcnow()
    start = datetime(now.year, now.month, 22, 8, tzinfo=UTC)
    return Window(id=f"{now.year:04d}-{now.month:02d}", start=start, end=start + timedelta(days=6))
