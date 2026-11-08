"""Data retention and deletion.

Relies on the column-naming conventions documented at the top of db/schema.sql, so tables added
by future features are covered automatically:

- ``guild_id``                              -> row belongs to a server (purged when the bot leaves)
- ``discord_user_id`` / ``user_id``         -> row is about a Discord user (purged on request)
- ``created_by`` / ``added_by`` / ``assigned_by`` -> who configured it (anonymised to 0 on request)

Retention windows here must match the published Privacy Policy (docs/privacy-policy.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from clashhero.db import Database

USER_COLUMNS = ("discord_user_id", "user_id")
CREATOR_COLUMNS = ("created_by", "added_by", "assigned_by")

# Retention windows (keep in sync with docs/privacy-policy.md).
ACTIVITY_DAYS = 90
HISTORY_DAYS = 400  # ~13 months of war, raid, legend, season and membership history
STALE_PLAYER_DAYS = 180
SNAPSHOT_DAYS = 30
DEPARTURE_GRACE_DAYS = 7


@dataclass
class PurgeReport:
    rows: dict[str, int] = field(default_factory=dict)

    def add(self, table: str, n: int) -> None:
        if n > 0:
            self.rows[table] = self.rows.get(table, 0) + n

    @property
    def total(self) -> int:
        return sum(self.rows.values())


async def _tables_with_column(db: Database, column: str) -> list[str]:
    tables = [
        r[0]
        for r in await db.fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    out = []
    for t in tables:
        cols = {r[1] for r in await db.fetchall(f"PRAGMA table_info({t})")}
        if column in cols:
            out.append(t)
    return sorted(out)


# ------------------------------------------------------------------ users


async def user_data_summary(db: Database, user_id: int) -> dict[str, int]:
    """Row counts per table that identify this Discord user (for /privacy mydata)."""
    out: dict[str, int] = {}
    for column in USER_COLUMNS + CREATOR_COLUMNS:
        for table in await _tables_with_column(db, column):
            n = await db.fetchval(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (user_id,), 0)
            if n:
                key = table if column in USER_COLUMNS else f"{table} (created by you)"
                out[key] = out.get(key, 0) + n
    return out


async def purge_user(db: Database, user_id: int) -> PurgeReport:
    """Delete every row about a Discord user and anonymise rows they created.

    Server configuration a user created (reminders, flags, bases, ...) belongs to that server, so
    it is kept but no longer attributed to them.
    """
    report = PurgeReport()
    async with db.batch():
        for column in USER_COLUMNS:
            for table in await _tables_with_column(db, column):
                n = await db.execute_rowcount(f"DELETE FROM {table} WHERE {column} = ?", (user_id,))
                report.add(table, n)
        for column in CREATOR_COLUMNS:
            for table in await _tables_with_column(db, column):
                n = await db.execute_rowcount(
                    f"UPDATE {table} SET {column} = 0 WHERE {column} = ?", (user_id,)
                )
                report.add(f"{table} (anonymised)", n)
    return report


# ------------------------------------------------------------------ servers


async def purge_guild(db: Database, guild_id: int) -> PurgeReport:
    """Delete all configuration and server-scoped data for a server.

    Child rows (base usages, roster members, reminder sends, ...) go via ON DELETE CASCADE.
    """
    report = PurgeReport()
    async with db.batch():
        for table in await _tables_with_column(db, "guild_id"):
            n = await db.execute_rowcount(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
            report.add(table, n)
    return report


async def known_guild_ids(db: Database) -> set[int]:
    ids: set[int] = set()
    for table in await _tables_with_column(db, "guild_id"):
        if table == "guild_departures":
            continue
        ids.update(r[0] for r in await db.fetchall(f"SELECT DISTINCT guild_id FROM {table}"))
    return ids


async def note_departures(db: Database, current_guild_ids: set[int], now: datetime) -> set[int]:
    """Record servers we have data for but are no longer in; clear ones we're back in."""
    missing = await known_guild_ids(db) - current_guild_ids
    async with db.batch():
        for gid in missing:
            await db.execute(
                "INSERT OR IGNORE INTO guild_departures (guild_id, noticed_at) VALUES (?, ?)",
                (gid, now.isoformat()),
            )
        for gid in current_guild_ids:
            await db.execute("DELETE FROM guild_departures WHERE guild_id = ?", (gid,))
    return missing


async def purge_departed_guilds(db: Database, now: datetime) -> list[int]:
    cutoff = (now - timedelta(days=DEPARTURE_GRACE_DAYS)).isoformat()
    rows = await db.fetchall("SELECT guild_id FROM guild_departures WHERE noticed_at <= ?", (cutoff,))
    purged = []
    for r in rows:
        await purge_guild(db, r["guild_id"])
        await db.execute("DELETE FROM guild_departures WHERE guild_id = ?", (r["guild_id"],))
        purged.append(r["guild_id"])
    return purged


# ------------------------------------------------------------------ retention


async def purge_old_data(db: Database, now: datetime | None = None) -> PurgeReport:
    """Apply the retention windows from the Privacy Policy."""
    now = now or datetime.now(UTC)
    report = PurgeReport()
    iso = lambda days: (now - timedelta(days=days)).isoformat()  # noqa: E731
    hist = iso(HISTORY_DAYS)
    hist_month = (now - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m")
    activity_hour = (now - timedelta(days=ACTIVITY_DAYS)).strftime("%Y-%m-%dT%H")
    statements = [
        ("activity", "DELETE FROM activity WHERE hour < ?", (activity_hour,)),
        ("legend_events", "DELETE FROM legend_events WHERE occurred_at < ?", (hist,)),
        ("legend_days", "DELETE FROM legend_days WHERE legend_day < ?", (hist[:10],)),
        ("clan_membership_events", "DELETE FROM clan_membership_events WHERE occurred_at < ?", (hist,)),
        ("season_stats", "DELETE FROM season_stats WHERE season_id < ?", (hist[:10],)),
        ("clan_games_points", "DELETE FROM clan_games_points WHERE games_id < ?", (hist_month,)),
        ("raid_members", "DELETE FROM raid_members WHERE start_time < ?", (hist,)),
        ("raid_seasons", "DELETE FROM raid_seasons WHERE start_time < ?", (hist,)),
        ("wars", "DELETE FROM wars WHERE preparation_start < ?", (hist,)),  # cascades to attacks
        ("snapshot_cache", "DELETE FROM snapshot_cache WHERE updated_at < ?", (iso(SNAPSHOT_DAYS),)),
        (
            "players",
            "DELETE FROM players WHERE updated_at < ? AND player_tag NOT IN "
            "(SELECT player_tag FROM player_links)",
            (iso(STALE_PLAYER_DAYS),),
        ),
        (
            "player_names",
            "DELETE FROM player_names WHERE player_tag NOT IN (SELECT player_tag FROM players)",
            (),
        ),
    ]
    async with db.batch():
        for table, sql, params in statements:
            report.add(table, await db.execute_rowcount(sql, params))
    return report
