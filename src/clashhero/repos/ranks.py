"""Global player ranks (only the top 200 exist in the Clash API)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
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
