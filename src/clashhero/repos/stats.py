"""Read-only queries over collected data for info, summary and export features."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from clashhero.db.database import Database, Row


def _in(values: Sequence[str]) -> str:
    return ",".join("?" * len(values)) or "NULL"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ------------------------------------------------------------------ members


async def join_dates(db: Database, clan_tag: str) -> dict[str, str]:
    """Most recent observed join time per player for a clan."""
    rows = await db.fetchall(
        "SELECT player_tag, MAX(occurred_at) AS joined FROM clan_membership_events "
        "WHERE clan_tag = ? AND kind = 'join' GROUP BY player_tag",
        (clan_tag,),
    )
    return {r["player_tag"]: r["joined"] for r in rows}


async def membership_history(db: Database, player_tag: str) -> list[Row]:
    return await db.fetchall(
        "SELECT e.*, c.name AS clan_name FROM clan_membership_events e "
        "LEFT JOIN clans c ON c.clan_tag = e.clan_tag WHERE e.player_tag = ? "
        "ORDER BY e.occurred_at DESC LIMIT 50",
        (player_tag,),
    )


async def lastseen_rows(db: Database, clan_tag: str, now: datetime) -> list[Row]:
    """Clan members with last_seen and activity count over the past 24h."""
    since = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H")
    return await db.fetchall(
        "SELECT p.player_tag, p.name, p.town_hall, p.last_seen, "
        "COALESCE((SELECT SUM(a.count) FROM activity a WHERE a.player_tag = p.player_tag "
        "AND a.hour >= ?), 0) AS day_activity "
        "FROM players p WHERE p.clan_tag = ? "
        "ORDER BY p.last_seen IS NULL, p.last_seen DESC",
        (since, clan_tag),
    )


async def activity_hours(db: Database, clan_tags: Sequence[str], since: datetime) -> list[tuple[str, int]]:
    """(hour bucket "YYYY-MM-DDTHH", total count) for players currently in ``clan_tags``."""
    rows = await db.fetchall(
        f"SELECT a.hour, SUM(a.count) AS n FROM activity a "
        f"JOIN players p ON p.player_tag = a.player_tag "
        f"WHERE p.clan_tag IN ({_in(clan_tags)}) AND a.hour >= ? GROUP BY a.hour",
        (*clan_tags, since.strftime("%Y-%m-%dT%H")),
    )
    return [(r["hour"], r["n"]) for r in rows]


# ------------------------------------------------------------------ season stats


async def clan_donations(db: Database, clan_tag: str, season_id: str) -> list[Row]:
    return await db.fetchall(
        "SELECT s.player_tag, COALESCE(p.name, s.player_tag) AS name, s.donations, s.received "
        "FROM season_stats s LEFT JOIN players p ON p.player_tag = s.player_tag "
        "WHERE s.season_id = ? AND s.clan_tag = ? AND (s.donations > 0 OR s.received > 0) "
        "ORDER BY s.donations DESC, s.received ASC",
        (season_id, clan_tag),
    )


async def player_season_totals(db: Database, player_tag: str, season_id: str) -> Row | None:
    return await db.fetchone(
        "SELECT SUM(donations) AS donations, SUM(received) AS received, "
        "SUM(capital_gold) AS capital_gold, SUM(attack_wins) AS attack_wins, "
        "SUM(activity) AS activity FROM season_stats WHERE season_id = ? AND player_tag = ?",
        (season_id, player_tag),
    )


async def season_totals_by_clan(db: Database, clan_tags: Sequence[str], season_id: str) -> list[Row]:
    return await db.fetchall(
        f"SELECT clan_tag, SUM(donations) AS donations, SUM(received) AS received, "
        f"SUM(capital_gold) AS capital_gold, SUM(activity) AS activity "
        f"FROM season_stats WHERE season_id = ? AND clan_tag IN ({_in(clan_tags)}) "
        f"GROUP BY clan_tag ORDER BY donations DESC",
        (season_id, *clan_tags),
    )


async def top_season_players(
    db: Database, clan_tags: Sequence[str], season_id: str, column: str, limit: int = 10
) -> list[Row]:
    if column not in {"donations", "received", "capital_gold", "attack_wins", "activity"}:
        raise ValueError(column)
    return await db.fetchall(
        f"SELECT s.player_tag, COALESCE(p.name, s.player_tag) AS name, s.clan_tag, "
        f"SUM(s.{column}) AS value FROM season_stats s "
        f"LEFT JOIN players p ON p.player_tag = s.player_tag "
        f"WHERE s.season_id = ? AND s.clan_tag IN ({_in(clan_tags)}) "
        f"GROUP BY s.player_tag HAVING value > 0 ORDER BY value DESC LIMIT ?",
        (season_id, *clan_tags, limit),
    )


async def season_stats_rows(db: Database, clan_tags: Sequence[str], season_id: str) -> list[Row]:
    return await db.fetchall(
        f"SELECT s.*, COALESCE(p.name, s.player_tag) AS name, p.town_hall "
        f"FROM season_stats s LEFT JOIN players p ON p.player_tag = s.player_tag "
        f"WHERE s.season_id = ? AND s.clan_tag IN ({_in(clan_tags)}) "
        f"ORDER BY s.clan_tag, s.donations DESC",
        (season_id, *clan_tags),
    )


async def clan_trophy_summary(db: Database, clan_tags: Sequence[str]) -> list[Row]:
    return await db.fetchall(
        f"SELECT p.clan_tag, c.name, COUNT(*) AS members, SUM(p.trophies) AS total, "
        f"AVG(p.trophies) AS average, MAX(p.trophies) AS best "
        f"FROM players p LEFT JOIN clans c ON c.clan_tag = p.clan_tag "
        f"WHERE p.clan_tag IN ({_in(clan_tags)}) GROUP BY p.clan_tag ORDER BY total DESC",
        tuple(clan_tags),
    )


async def top_trophy_players(db: Database, clan_tags: Sequence[str], limit: int = 15) -> list[Row]:
    return await db.fetchall(
        f"SELECT player_tag, name, town_hall, trophies, clan_tag FROM players "
        f"WHERE clan_tag IN ({_in(clan_tags)}) ORDER BY trophies DESC LIMIT ?",
        (*clan_tags, limit),
    )


# ------------------------------------------------------------------ wars


async def current_wars(db: Database, clan_tags: Sequence[str]) -> list[Row]:
    """The latest war per clan that isn't over."""
    return await db.fetchall(
        f"SELECT w.*, c.name AS clan_name FROM wars w LEFT JOIN clans c ON c.clan_tag = w.clan_tag "
        f"WHERE w.clan_tag IN ({_in(clan_tags)}) AND w.state != 'warEnded' AND w.id = "
        f"(SELECT id FROM wars w2 WHERE w2.clan_tag = w.clan_tag "
        f"ORDER BY w2.preparation_start DESC LIMIT 1)",
        tuple(clan_tags),
    )


async def war_results(db: Database, clan_tags: Sequence[str], start: datetime, end: datetime) -> list[Row]:
    """Win/lose/tie counts per clan for wars that started in the window."""
    return await db.fetchall(
        f"SELECT w.clan_tag, c.name AS clan_name, "
        f"SUM(w.result = 'win') AS wins, SUM(w.result = 'lose') AS losses, "
        f"SUM(w.result = 'tie') AS ties, COUNT(*) AS total "
        f"FROM wars w LEFT JOIN clans c ON c.clan_tag = w.clan_tag "
        f"WHERE w.state = 'warEnded' AND w.clan_tag IN ({_in(clan_tags)}) "
        f"AND w.preparation_start >= ? AND w.preparation_start < ? "
        f"GROUP BY w.clan_tag ORDER BY wins DESC",
        (*clan_tags, _iso(start), _iso(end)),
    )


async def missed_attacks(db: Database, clan_tags: Sequence[str], start: datetime, end: datetime) -> list[Row]:
    """One row per (war, member) with unused attacks, for ended wars in the window."""
    return await db.fetchall(
        f"SELECT w.id AS war_id, w.clan_tag, w.war_type, w.opponent_name, w.end_time, "
        f"wm.player_tag, wm.name, wm.town_hall, "
        f"w.attacks_per_member - COUNT(a.attack_order) AS missed "
        f"FROM wars w JOIN war_members wm ON wm.war_id = w.id AND wm.is_opponent = 0 "
        f"LEFT JOIN war_attacks a ON a.war_id = w.id AND a.attacker_tag = wm.player_tag "
        f"WHERE w.state = 'warEnded' AND w.clan_tag IN ({_in(clan_tags)}) "
        f"AND w.preparation_start >= ? AND w.preparation_start < ? "
        f"GROUP BY w.id, wm.player_tag HAVING missed > 0 ORDER BY w.end_time DESC",
        (*clan_tags, _iso(start), _iso(end)),
    )


async def wars_rows(db: Database, clan_tags: Sequence[str], start: datetime, end: datetime) -> list[Row]:
    return await db.fetchall(
        f"SELECT * FROM wars WHERE clan_tag IN ({_in(clan_tags)}) "
        f"AND preparation_start >= ? AND preparation_start < ? ORDER BY preparation_start",
        (*clan_tags, _iso(start), _iso(end)),
    )


async def attack_rows(
    db: Database, clan_tags: Sequence[str], start: datetime, end: datetime, *, cwl_only: bool = False
) -> list[Row]:
    """Our clans' attacks (not the opponent's) with names and war context."""
    cwl = "AND w.war_type = 'cwl'" if cwl_only else ""
    return await db.fetchall(
        f"SELECT w.clan_tag, w.war_type, w.opponent_name, w.cwl_season, w.cwl_round, "
        f"w.preparation_start, a.*, am.name AS attacker_name, dm.name AS defender_name "
        f"FROM war_attacks a JOIN wars w ON w.id = a.war_id "
        f"LEFT JOIN war_members am ON am.war_id = a.war_id AND am.player_tag = a.attacker_tag "
        f"LEFT JOIN war_members dm ON dm.war_id = a.war_id AND dm.player_tag = a.defender_tag "
        f"WHERE a.is_opponent = 0 AND w.clan_tag IN ({_in(clan_tags)}) {cwl} "
        f"AND w.preparation_start >= ? AND w.preparation_start < ? "
        f"ORDER BY w.preparation_start, a.attack_order",
        (*clan_tags, _iso(start), _iso(end)),
    )


async def war_star_leaders(
    db: Database, clan_tags: Sequence[str], start: datetime, end: datetime, limit: int = 10
) -> list[Row]:
    return await db.fetchall(
        f"SELECT a.attacker_tag AS player_tag, COALESCE(p.name, a.attacker_tag) AS name, "
        f"SUM(a.new_stars) AS value FROM war_attacks a JOIN wars w ON w.id = a.war_id "
        f"LEFT JOIN players p ON p.player_tag = a.attacker_tag "
        f"WHERE a.is_opponent = 0 AND w.clan_tag IN ({_in(clan_tags)}) "
        f"AND w.preparation_start >= ? AND w.preparation_start < ? "
        f"GROUP BY a.attacker_tag HAVING value > 0 ORDER BY value DESC LIMIT ?",
        (*clan_tags, _iso(start), _iso(end), limit),
    )


# ------------------------------------------------------------------ capital & clan games


async def latest_raids(db: Database, clan_tags: Sequence[str]) -> list[Row]:
    """Most recent raid weekend per clan with member totals."""
    return await db.fetchall(
        f"SELECT r.*, c.name AS clan_name, "
        f"(SELECT COUNT(*) FROM raid_members m WHERE m.clan_tag = r.clan_tag "
        f"AND m.start_time = r.start_time) AS participants, "
        f"(SELECT SUM(m.attacks) FROM raid_members m WHERE m.clan_tag = r.clan_tag "
        f"AND m.start_time = r.start_time) AS member_attacks "
        f"FROM raid_seasons r LEFT JOIN clans c ON c.clan_tag = r.clan_tag "
        f"WHERE r.clan_tag IN ({_in(clan_tags)}) AND r.start_time = "
        f"(SELECT MAX(start_time) FROM raid_seasons r2 WHERE r2.clan_tag = r.clan_tag) "
        f"ORDER BY r.total_loot DESC",
        tuple(clan_tags),
    )


async def raid_member_rows(db: Database, clan_tags: Sequence[str], since: datetime) -> list[Row]:
    return await db.fetchall(
        f"SELECT * FROM raid_members WHERE clan_tag IN ({_in(clan_tags)}) AND start_time >= ? "
        f"ORDER BY start_time DESC, loot DESC",
        (*clan_tags, _iso(since)),
    )


async def clan_games_by_clan(db: Database, clan_tags: Sequence[str], games_id: str) -> list[Row]:
    return await db.fetchall(
        f"SELECT g.clan_tag, c.name AS clan_name, SUM(g.points) AS points, "
        f"COUNT(*) AS participants, SUM(g.points >= 4000) AS maxed "
        f"FROM clan_games_points g LEFT JOIN clans c ON c.clan_tag = g.clan_tag "
        f"WHERE g.games_id = ? AND g.clan_tag IN ({_in(clan_tags)}) "
        f"GROUP BY g.clan_tag ORDER BY points DESC",
        (games_id, *clan_tags),
    )


async def clan_games_players(
    db: Database, clan_tags: Sequence[str], games_id: str, limit: int | None = None
) -> list[Row]:
    lim = "LIMIT ?" if limit else ""
    params: tuple = (games_id, *clan_tags) + ((limit,) if limit else ())
    return await db.fetchall(
        f"SELECT g.*, COALESCE(p.name, g.player_tag) AS name FROM clan_games_points g "
        f"LEFT JOIN players p ON p.player_tag = g.player_tag "
        f"WHERE g.games_id = ? AND g.clan_tag IN ({_in(clan_tags)}) ORDER BY g.points DESC {lim}",
        params,
    )


# ------------------------------------------------------------------ users


async def guild_user_links(db: Database, clan_tags: Sequence[str]) -> list[Row]:
    """Linked accounts that are in (or were last seen in) the given clans, plus all links of
    those users."""
    return await db.fetchall(
        f"SELECT l.discord_user_id, l.player_tag, l.is_default, l.verified, l.linked_at, "
        f"p.name, p.town_hall, p.clan_tag FROM player_links l "
        f"LEFT JOIN players p ON p.player_tag = l.player_tag "
        f"WHERE l.discord_user_id IN (SELECT l2.discord_user_id FROM player_links l2 "
        f"JOIN players p2 ON p2.player_tag = l2.player_tag WHERE p2.clan_tag IN ({_in(clan_tags)})) "
        f"ORDER BY l.discord_user_id, l.is_default DESC",
        tuple(clan_tags),
    )
