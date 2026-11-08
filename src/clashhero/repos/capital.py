"""Capital raid weekends, capital gold contributions and Clan Games queries."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from clashhero.db import Database

# Default per-player Clan Games cap; some months raise it (e.g. 5000) — pass ``cap`` to override.
GAMES_PLAYER_CAP = 4000
# Cumulative clan points needed for each reward tier.
GAMES_TIERS = (3000, 7500, 12000, 18000, 30000, 50000)


# ------------------------------------------------------------------ raids


async def raid_weekends(db: Database, clan_tag: str, limit: int = 10) -> list[dict]:
    rows = await db.fetchall(
        "SELECT * FROM raid_seasons WHERE clan_tag = ? ORDER BY start_time DESC LIMIT ?",
        (clan_tag, limit),
    )
    return [dict(r) for r in rows]


async def raid_weekend(db: Database, clan_tag: str, weeks_back: int = 0) -> dict | None:
    rows = await raid_weekends(db, clan_tag, limit=weeks_back + 1)
    return rows[weeks_back] if len(rows) > weeks_back else None


async def raid_members(db: Database, clan_tag: str, start_time: str) -> list[dict]:
    rows = await db.fetchall(
        "SELECT * FROM raid_members WHERE clan_tag = ? AND start_time = ? ORDER BY loot DESC, attacks DESC",
        (clan_tag, start_time),
    )
    return [dict(r) for r in rows]


async def raid_non_participants(db: Database, clan_tag: str, start_time: str) -> list[dict]:
    """Current clan members (from players) with no raid attacks in that weekend."""
    rows = await db.fetchall(
        "SELECT p.player_tag, p.name, p.town_hall FROM players p WHERE p.clan_tag = ? "
        "AND p.player_tag NOT IN (SELECT player_tag FROM raid_members WHERE clan_tag = ? "
        "AND start_time = ? AND attacks > 0) ORDER BY p.name",
        (clan_tag, clan_tag, start_time),
    )
    return [dict(r) for r in rows]


async def player_raid_history(db: Database, player_tags: Sequence[str], limit: int = 12) -> list[dict]:
    if not player_tags:
        return []
    q = ",".join("?" * len(player_tags))
    rows = await db.fetchall(
        "SELECT m.*, s.state, s.total_loot FROM raid_members m LEFT JOIN raid_seasons s "
        "ON s.clan_tag = m.clan_tag AND s.start_time = m.start_time "
        f"WHERE m.player_tag IN ({q}) ORDER BY m.start_time DESC LIMIT ?",
        (*player_tags, limit),
    )
    return [dict(r) for r in rows]


async def capital_contributions(db: Database, clan_tag: str, season_id: str) -> list[dict]:
    rows = await db.fetchall(
        "SELECT s.player_tag, s.capital_gold, COALESCE(p.name, s.player_tag) AS name "
        "FROM season_stats s LEFT JOIN players p ON p.player_tag = s.player_tag "
        "WHERE s.season_id = ? AND s.clan_tag = ? AND s.capital_gold > 0 "
        "ORDER BY s.capital_gold DESC",
        (season_id, clan_tag),
    )
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ clan games


@dataclass
class GamesSummary:
    rows: list[dict]  # player_tag, name, points, counted, maxed
    total: int  # clan points (each player's contribution capped)
    tier: int  # reward tiers unlocked (0-6)
    next_tier_at: int | None  # points needed for the next tier, None when maxed
    cap: int


def games_tier(total: int) -> tuple[int, int | None]:
    tier = sum(1 for t in GAMES_TIERS if total >= t)
    return tier, (GAMES_TIERS[tier] if tier < len(GAMES_TIERS) else None)


def summarize_games(rows: Sequence[dict], cap: int = GAMES_PLAYER_CAP) -> GamesSummary:
    out = []
    for r in rows:
        counted = min(r["points"], cap)
        out.append({**r, "counted": counted, "maxed": r["points"] >= cap})
    out.sort(key=lambda r: (-r["points"], r["name"]))
    total = sum(r["counted"] for r in out)
    tier, nxt = games_tier(total)
    return GamesSummary(out, total, tier, nxt, cap)


async def clan_games_rows(db: Database, clan_tag: str, games_id: str) -> list[dict]:
    rows = await db.fetchall(
        "SELECT g.player_tag, g.points, COALESCE(p.name, g.player_tag) AS name "
        "FROM clan_games_points g LEFT JOIN players p ON p.player_tag = g.player_tag "
        "WHERE g.games_id = ? AND g.clan_tag = ?",
        (games_id, clan_tag),
    )
    return [dict(r) for r in rows]


async def clan_games_missing(db: Database, clan_tag: str, games_id: str) -> list[dict]:
    """Current members with no recorded Clan Games points this month."""
    rows = await db.fetchall(
        "SELECT player_tag, name, town_hall FROM players WHERE clan_tag = ? AND player_tag NOT IN "
        "(SELECT player_tag FROM clan_games_points WHERE games_id = ?) ORDER BY name",
        (clan_tag, games_id),
    )
    return [dict(r) for r in rows]
