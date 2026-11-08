"""What a player's accounts still have to do right now (/todo), and inactive members (/inactive).

Everything reads collected tables only (wars, raids, Clan Games points, legend events, players).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from clashhero.db.database import Database, Row
from clashhero.utils.ranked import daily_attack_cap, is_tracked_ranked, weekly_attack_cap
from clashhero.utils.seasons import clan_games, legend_day_bounds, legend_day_id, ranked_week

GAMES_CAP = 4000
RAID_BASE_ATTACKS = 5
KIND_ORDER = ("war", "cwl", "ranked", "raid", "games")


def _dt(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    d = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _in(values: Sequence[Any]) -> str:
    return ",".join("?" * len(values))


@dataclass
class TodoItem:
    """One thing an account can still do. ``total`` None means there's no cap (just a count)."""

    kind: str  # war | cwl | ranked | raid | games
    label: str  # clan name, or the ranked league
    done: int
    total: int | None
    ends_at: datetime | None = None
    period: str = ""  # ranked: "today" or "this week"

    @property
    def left(self) -> int | None:
        return None if self.total is None else max(0, self.total - self.done)

    @property
    def pending(self) -> bool:
        return bool(self.left)


@dataclass
class AccountTodo:
    player_tag: str
    name: str
    town_hall: int | None
    items: list[TodoItem] = field(default_factory=list)

    @property
    def all_done(self) -> bool:
        return not any(i.pending for i in self.items)


# ------------------------------------------------------------------ pure helpers


def ranked_item(
    league: str | None, today_attacks: int, week_attacks: int, now: datetime, first_day: bool = False
) -> TodoItem | None:
    """Legend I: today vs the daily cap. Legend II/III: this week vs the weekly cap. Other tracked
    tiers: today's count with no cap. None when untracked or nothing is left."""
    if not is_tracked_ranked(league):
        return None
    if (cap := daily_attack_cap(league, first_day=first_day)) is not None:
        item = TodoItem("ranked", league, today_attacks, cap, legend_day_bounds(now)[1], "today")
    elif (cap := weekly_attack_cap(league)) is not None:
        item = TodoItem("ranked", league, week_attacks, cap, ranked_week(now).end, "this week")
    else:
        return TodoItem("ranked", league, today_attacks, None, legend_day_bounds(now)[1], "today")
    return item if item.pending else None


def sort_items(items: Iterable[TodoItem]) -> list[TodoItem]:
    return sorted(items, key=lambda i: KIND_ORDER.index(i.kind) if i.kind in KIND_ORDER else 99)


def pick_inactive(rows: Iterable[Mapping[str, Any]], now: datetime, days: int) -> list[Mapping[str, Any]]:
    """Rows whose ``last_seen`` is unknown or older than ``days``; unknown first, then oldest."""
    cutoff = now - timedelta(days=days)
    out = [r for r in rows if (seen := _dt(r["last_seen"])) is None or seen < cutoff]
    floor = datetime.min.replace(tzinfo=UTC)
    return sorted(out, key=lambda r: (_dt(r["last_seen"]) or floor, (r["name"] or "").lower()))


# ------------------------------------------------------------------ queries


async def player_rows(db: Database, tags: Sequence[str]) -> dict[str, Row]:
    if not tags:
        return {}
    rows = await db.fetchall(f"SELECT * FROM players WHERE player_tag IN ({_in(tags)})", list(tags))
    return {r["player_tag"]: r for r in rows}


async def war_items(db: Database, tags: Sequence[str], now: datetime) -> dict[str, list[TodoItem]]:
    """Attacks left in wars/CWL rounds in battle day where the player is on our side."""
    if not tags:
        return {}
    rows = await db.fetchall(
        "SELECT m.player_tag, w.war_type, w.attacks_per_member, w.end_time, w.clan_tag, "
        "COALESCE(c.name, w.clan_tag) AS clan_name, "
        "(SELECT COUNT(*) FROM war_attacks a WHERE a.war_id = w.id "
        " AND a.attacker_tag = m.player_tag) AS used "
        "FROM war_members m JOIN wars w ON w.id = m.war_id "
        "LEFT JOIN clans c ON c.clan_tag = w.clan_tag "
        f"WHERE m.is_opponent = 0 AND w.state = 'inWar' AND m.player_tag IN ({_in(tags)}) "
        "ORDER BY w.preparation_start DESC",
        list(tags),
    )
    out: dict[str, list[TodoItem]] = {}
    seen: set[tuple[str, bool]] = set()
    for r in rows:
        end = _dt(r["end_time"])
        if end is not None and end <= now:
            continue  # stale: the war is over even if we haven't seen it end
        cwl = r["war_type"] == "cwl"
        if (r["player_tag"], cwl) in seen:
            continue  # keep the latest war of each kind
        seen.add((r["player_tag"], cwl))
        item = TodoItem("cwl" if cwl else "war", r["clan_name"], r["used"], r["attacks_per_member"] or 1, end)
        if item.pending:
            out.setdefault(r["player_tag"], []).append(item)
    return out


async def ranked_items(
    db: Database, players: Mapping[str, Mapping[str, Any]], now: datetime
) -> dict[str, list[TodoItem]]:
    tags = [t for t, p in players.items() if is_tracked_ranked(p["league"])]
    if not tags:
        return {}
    week = ranked_week(now)
    today = legend_day_id(now)
    rows = await db.fetchall(
        "SELECT player_tag, SUM(legend_day = ?) AS today, COUNT(*) AS week FROM legend_events "
        f"WHERE kind = 'attack' AND legend_day >= ? AND legend_day < ? AND player_tag IN ({_in(tags)}) "
        "GROUP BY player_tag",
        (today, week.start.date().isoformat(), week.end.date().isoformat(), *tags),
    )
    counts = {r["player_tag"]: (r["today"] or 0, r["week"] or 0) for r in rows}
    promoted = {
        r["player_tag"]
        for r in await db.fetchall(
            f"SELECT player_tag FROM legend_promotions WHERE legend_day = ? AND player_tag IN ({_in(tags)})",
            (today, *tags),
        )
    }
    out: dict[str, list[TodoItem]] = {}
    for tag in tags:
        day_n, week_n = counts.get(tag, (0, 0))
        item = ranked_item(players[tag]["league"], day_n, week_n, now, first_day=tag in promoted)
        if item is not None:
            out[tag] = [item]
    return out


async def raid_items(
    db: Database, players: Mapping[str, Mapping[str, Any]], now: datetime
) -> dict[str, list[TodoItem]]:
    """Raid attacks left in an ongoing raid weekend: the player's raid row if they've raided,
    else 0 of 5 when their current clan is tracked and mid-raid."""
    tags = list(players)
    if not tags:
        return {}
    raided = await db.fetchall(
        "SELECT r.player_tag, r.attacks, r.attack_limit, s.end_time, "
        "COALESCE(c.name, r.clan_tag) AS clan_name FROM raid_members r "
        "JOIN raid_seasons s ON s.clan_tag = r.clan_tag AND s.start_time = r.start_time "
        "LEFT JOIN clans c ON c.clan_tag = r.clan_tag "
        f"WHERE s.state = 'ongoing' AND r.player_tag IN ({_in(tags)})",
        tags,
    )
    out: dict[str, list[TodoItem]] = {}
    has_row: set[str] = set()
    for r in raided:
        end = _dt(r["end_time"])
        if end is not None and end <= now:
            continue
        has_row.add(r["player_tag"])
        item = TodoItem("raid", r["clan_name"], r["attacks"], r["attack_limit"], end)
        if item.pending:
            out.setdefault(r["player_tag"], []).append(item)
    clans = sorted({p["clan_tag"] for t, p in players.items() if p["clan_tag"] and t not in has_row})
    if not clans:
        return out
    ongoing = {
        r["clan_tag"]: r
        for r in await db.fetchall(
            "SELECT s.clan_tag, s.end_time, COALESCE(c.name, s.clan_tag) AS clan_name "
            "FROM raid_seasons s LEFT JOIN clans c ON c.clan_tag = s.clan_tag "
            f"WHERE s.state = 'ongoing' AND s.clan_tag IN ({_in(clans)}) ORDER BY s.start_time",
            clans,
        )
    }
    for tag, p in players.items():
        raid = ongoing.get(p["clan_tag"]) if tag not in has_row else None
        end = _dt(raid["end_time"]) if raid else None
        if raid is None or (end is not None and end <= now):
            continue
        out.setdefault(tag, []).append(TodoItem("raid", raid["clan_name"], 0, RAID_BASE_ATTACKS, end))
    return out


async def games_items(
    db: Database, players: Mapping[str, Mapping[str, Any]], now: datetime, cap: int = GAMES_CAP
) -> dict[str, list[TodoItem]]:
    """Clan Games points below the cap while the games are running (players in a clan only)."""
    window = clan_games(now)
    tags = [t for t, p in players.items() if p["clan_tag"]]
    if not window.contains(now) or not tags:
        return {}
    points = {
        r["player_tag"]: r["points"]
        for r in await db.fetchall(
            f"SELECT player_tag, points FROM clan_games_points WHERE games_id = ? "
            f"AND player_tag IN ({_in(tags)})",
            (window.id, *tags),
        )
    }
    out: dict[str, list[TodoItem]] = {}
    for tag in tags:
        item = TodoItem("games", "", points.get(tag, 0), cap, window.end)
        if item.pending:
            out[tag] = [item]
    return out


async def account_todos(db: Database, tags: Sequence[str], now: datetime) -> list[AccountTodo]:
    """Everything left for each account, in ``tags`` order."""
    tags = list(dict.fromkeys(tags))
    players = await player_rows(db, tags)
    found: dict[str, list[TodoItem]] = {t: [] for t in tags}
    for part in (
        await war_items(db, tags, now),
        await ranked_items(db, players, now),
        await raid_items(db, players, now),
        await games_items(db, players, now),
    ):
        for tag, items in part.items():
            found[tag].extend(items)
    out = []
    for tag in tags:
        p = players.get(tag)
        out.append(
            AccountTodo(
                tag,
                p["name"] if p else tag,
                p["town_hall"] if p else None,
                sort_items(found[tag]),
            )
        )
    return out


async def clan_member_activity(db: Database, clan_tag: str) -> list[Row]:
    return await db.fetchall(
        "SELECT player_tag, name, town_hall, clan_role, last_seen FROM players WHERE clan_tag = ?",
        (clan_tag,),
    )


async def inactive_members(db: Database, clan_tag: str, now: datetime, days: int) -> list[Mapping[str, Any]]:
    return pick_inactive(await clan_member_activity(db, clan_tag), now, days)
