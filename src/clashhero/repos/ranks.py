"""Player ranks: the global top 200 and each country's top 200 (the only rankings the Clash API
publishes)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from clashhero.db.database import Database

# A rank older than this is treated as unknown (the bot was down, or the API stopped answering).
MAX_RANK_AGE = timedelta(hours=2)


async def store_rankings(
    db: Database, items: Iterable[Mapping[str, Any]], legend_day: str, now: datetime
) -> int:
    """Replace the current ranking with ``items`` (the API's ``/locations/global/rankings/players``)
    and record each player's rank for ``legend_day``. Returns how many were stored."""
    rows = [
        (i["tag"], int(i["rank"]), int(i.get("trophies") or 0))
        for i in items
        if i.get("tag") and isinstance(i.get("rank"), int)
    ]
    if not rows:
        return 0
    fetched = now.isoformat()
    async with db.batch():
        await db.execute("DELETE FROM player_ranks")
        await db.executemany(
            "INSERT INTO player_ranks (player_tag, rank, trophies, fetched_at) VALUES (?, ?, ?, ?)",
            [(*r, fetched) for r in rows],
        )
        await db.executemany(
            "INSERT INTO legend_day_ranks (player_tag, legend_day, rank, trophies) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (player_tag, legend_day) DO UPDATE SET rank = excluded.rank, "
            "trophies = excluded.trophies",
            [(tag, legend_day, rank, trophies) for tag, rank, trophies in rows],
        )
        # Players who dropped out of the top 200 during the day no longer have a rank for it.
        placeholders = ",".join("?" * len(rows))
        await db.execute(
            f"DELETE FROM legend_day_ranks WHERE legend_day = ? AND player_tag NOT IN ({placeholders})",
            (legend_day, *(r[0] for r in rows)),
        )
    return len(rows)


async def current_rank(db: Database, tag: str, now: datetime | None = None) -> int | None:
    """The player's global rank from the latest fetch, if they're in the top 200 and it's fresh."""
    row = await db.fetchone("SELECT rank, fetched_at FROM player_ranks WHERE player_tag = ?", (tag,))
    if row is None:
        return None
    if now is not None and now - datetime.fromisoformat(row["fetched_at"]) > MAX_RANK_AGE:
        return None
    return row["rank"]


async def eod_rank(db: Database, tag: str, legend_day: str) -> int | None:
    """The player's global rank at the end of ``legend_day`` (last fetch before the reset)."""
    return await db.fetchval(
        "SELECT rank FROM legend_day_ranks WHERE player_tag = ? AND legend_day = ?", (tag, legend_day)
    )


# ------------------------------------------------------------------ country leaderboards

# Country rankings are fetched every few hours; older than this counts as unknown.
MAX_COUNTRY_RANK_AGE = timedelta(hours=8)


@dataclass(frozen=True)
class CountryRank:
    rank: int
    country_code: str  # ISO 3166-1 alpha-2, e.g. "US"
    country_name: str


async def store_country_rankings(
    db: Database, location: Mapping[str, Any], items: Iterable[Mapping[str, Any]], now: datetime
) -> int:
    """Replace one country's top 200 (``/locations/{id}/rankings/players``). ``location`` is its
    entry from ``/locations`` (id, name, countryCode). Returns how many players were stored."""
    code, name, loc_id = location.get("countryCode"), location.get("name"), location.get("id")
    if not code or not name or not isinstance(loc_id, int):
        return 0
    rows = [
        (i["tag"], loc_id, code, name, int(i["rank"]), int(i.get("trophies") or 0), now.isoformat())
        for i in items
        if i.get("tag") and isinstance(i.get("rank"), int)
    ]
    if not rows:
        return 0
    async with db.batch():
        await db.execute("DELETE FROM player_country_ranks WHERE location_id = ?", (loc_id,))
        await db.executemany(
            "INSERT INTO player_country_ranks (player_tag, location_id, country_code, country_name, "
            "rank, trophies, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (player_tag) DO "
            "UPDATE SET location_id = excluded.location_id, country_code = excluded.country_code, "
            "country_name = excluded.country_name, rank = excluded.rank, "
            "trophies = excluded.trophies, fetched_at = excluded.fetched_at",
            rows,
        )
    return len(rows)


async def country_rank(db: Database, tag: str, now: datetime | None = None) -> CountryRank | None:
    """The player's country and rank in it, if they're in their country's top 200."""
    row = await db.fetchone(
        "SELECT rank, country_code, country_name, fetched_at FROM player_country_ranks WHERE player_tag = ?",
        (tag,),
    )
    if row is None:
        return None
    if now is not None and now - datetime.fromisoformat(row["fetched_at"]) > MAX_COUNTRY_RANK_AGE:
        return None
    return CountryRank(row["rank"], row["country_code"], row["country_name"])
