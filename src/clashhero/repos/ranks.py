"""Player ranks: the global top 200 and each country's top 200 (the only rankings the Clash API
publishes)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
    """The player's global rank at the end of ``legend_day``: from ClashHero's own full Legend I
    ranking when that day has one, else from the API's top 200 (last fetch before the reset)."""
    full = await db.fetchval(
        "SELECT rank FROM legend_day_full_ranks WHERE player_tag = ? AND legend_day = ?", (tag, legend_day)
    )
    if full is not None:
        return full
    return await db.fetchval(
        "SELECT rank FROM legend_day_ranks WHERE player_tag = ? AND legend_day = ?", (tag, legend_day)
    )


async def eod_ranks(db: Database, tag: str, first_day: str, last_day: str) -> dict[str, int]:
    """The player's end-of-day global rank for each legend day from ``first_day`` to ``last_day``
    that has one (as :func:`eod_rank`: ClashHero's own full ranking first, else the top 200)."""
    out: dict[str, int] = {}
    for table in ("legend_day_ranks", "legend_day_full_ranks"):  # the full ranking wins
        rows = await db.fetchall(
            f"SELECT legend_day, rank FROM {table} WHERE player_tag = ? AND legend_day BETWEEN ? AND ?",
            (tag, first_day, last_day),
        )
        out.update({r["legend_day"]: r["rank"] for r in rows})
    return out


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
        (
            i["tag"],
            loc_id,
            code,
            name,
            int(i["rank"]),
            int(i.get("trophies") or 0),
            now.isoformat(),
            (i.get("leagueTier") or {}).get("name"),
        )
        for i in items
        if i.get("tag") and isinstance(i.get("rank"), int)
    ]
    if not rows:
        return 0
    async with db.batch():
        await db.execute("DELETE FROM player_country_ranks WHERE location_id = ?", (loc_id,))
        await db.executemany(
            "INSERT INTO player_country_ranks (player_tag, location_id, country_code, country_name, "
            "rank, trophies, fetched_at, league) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT "
            "(player_tag) DO UPDATE SET location_id = excluded.location_id, "
            "country_code = excluded.country_code, country_name = excluded.country_name, "
            "rank = excluded.rank, trophies = excluded.trophies, fetched_at = excluded.fetched_at, "
            "league = excluded.league",
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


# ------------------------------------------------------------------ season finishes


@dataclass(frozen=True)
class Finish:
    rank: int
    trophies: int
    season_id: str


async def stored_seasons(db: Database) -> set[str]:
    rows = await db.fetchall("SELECT DISTINCT season_id FROM season_finishes")
    return {r["season_id"] for r in rows}


async def store_season_finishes(db: Database, season_id: str, items: Iterable[Mapping[str, Any]]) -> int:
    """Store a finished season's whole final ranking (all pages). Returns the number of players."""
    rows = [
        (season_id, i["tag"], int(i["rank"]), int(i.get("trophies") or 0))
        for i in items
        if i.get("tag") and isinstance(i.get("rank"), int)
    ]
    if not rows:
        return 0
    async with db.batch():
        await db.execute("DELETE FROM season_finishes WHERE season_id = ?", (season_id,))
        await db.executemany(
            "INSERT INTO season_finishes (season_id, player_tag, rank, trophies) VALUES (?, ?, ?, ?)", rows
        )
    return len(rows)


async def best_finish(db: Database, tag: str) -> Finish | None:
    """The player's best season-end global rank in Legend I (of the seasons stored)."""
    row = await db.fetchone(
        "SELECT rank, trophies, season_id FROM season_finishes WHERE player_tag = ? "
        "ORDER BY rank, season_id DESC LIMIT 1",
        (tag,),
    )
    return Finish(row["rank"], row["trophies"], row["season_id"]) if row else None


# ------------------------------------------------------------------ ClashHero's daily Legend I ranking

FULL_RANK_DAYS = 62  # how long daily rankings are kept (/legends day reaches 60 days back)


def competition_ranks(players: Iterable[tuple[str, int]]) -> list[tuple[str, int, int]]:
    """(tag, rank, trophies) by trophies, highest first; equal trophies share a rank (1, 2, 2, 4)."""
    ordered = sorted(players, key=lambda p: (-p[1], p[0]))
    out: list[tuple[str, int, int]] = []
    for i, (tag, trophies) in enumerate(ordered):
        rank = out[-1][1] if out and out[-1][2] == trophies else i + 1
        out.append((tag, rank, trophies))
    return out


async def legend_one_candidates(db: Database, since_day: str) -> set[str]:
    """Players who are probably in Legend I: last finished season's finishers, Legend I players in
    the country and global leaderboards, tracked Legend I players, recent Legend I opponents, and
    whoever the previous daily ranking found. The job checks each one's current league."""
    queries = (
        "SELECT player_tag FROM season_finishes WHERE season_id = "
        "(SELECT MAX(season_id) FROM season_finishes)",
        "SELECT player_tag FROM player_country_ranks WHERE league = 'Legend I'",
        "SELECT player_tag FROM player_ranks",
        "SELECT player_tag FROM players WHERE league = 'Legend I'",
        "SELECT DISTINCT opponent_tag FROM legend_events WHERE battle_type = 'legend' "
        "AND opponent_tag IS NOT NULL AND legend_day >= ?",
        "SELECT player_tag FROM legend_day_full_ranks WHERE legend_day = "
        "(SELECT MAX(legend_day) FROM legend_day_full_ranks)",
    )
    tags: set[str] = set()
    for q in queries:
        params = (since_day,) if "?" in q else ()
        tags.update(r[0] for r in await db.fetchall(q, params) if r[0])
    return tags


async def store_full_ranking(db: Database, legend_day: str, ranked: Iterable[tuple[str, int, int]]) -> int:
    """Store one legend day's full Legend I ranking ((tag, rank, trophies)) and drop old days."""
    rows = [(tag, legend_day, rank, trophies) for tag, rank, trophies in ranked]
    if not rows:
        return 0
    cutoff = (date.fromisoformat(legend_day) - timedelta(days=FULL_RANK_DAYS)).isoformat()
    async with db.batch():
        await db.execute("DELETE FROM legend_day_full_ranks WHERE legend_day = ?", (legend_day,))
        await db.executemany(
            "INSERT INTO legend_day_full_ranks (player_tag, legend_day, rank, trophies) VALUES (?, ?, ?, ?)",
            rows,
        )
        await db.execute("DELETE FROM legend_day_full_ranks WHERE legend_day < ?", (cutoff,))
    return len(rows)
