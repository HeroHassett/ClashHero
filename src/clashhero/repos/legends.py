"""Queries and pure aggregation helpers for legend tracking."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from clashhero.db.database import Database, Row
from clashhero.utils.ranked import daily_attack_cap, is_tracked_ranked

BATTLES_PER_DAY = 8
MAX_HIT = 40
BOARD_LIMIT = 50


# ------------------------------------------------------------------ pure aggregation


def _col(row: Any, key: str) -> Any:
    """``row[key]`` for sqlite rows and dicts, None when the column is absent."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


@dataclass(frozen=True)
class Hit:
    """One ranked battle. The opponent/stars/destruction/army fields are only set for battles read
    from the battle log; battles inferred from trophy changes carry just the trophies."""

    kind: str  # attack | defense
    trophies: int
    occurred_at: str | None = None
    event_id: int | None = None
    opponent_tag: str | None = None
    opponent_name: str | None = None
    opponent_th: int | None = None
    stars: int | None = None
    destruction: int | None = None
    army_share_code: str | None = None

    @property
    def detailed(self) -> bool:
        return self.stars is not None

    @property
    def triple(self) -> bool:
        """3 stars; for battles without star data, a max-trophy attack is the best proxy."""
        return self.stars == 3 if self.stars is not None else self.trophies >= MAX_HIT

    @classmethod
    def from_row(cls, r: Any) -> Hit:
        return cls(
            kind=r["kind"],
            trophies=r["trophies"],
            occurred_at=_col(r, "occurred_at"),
            event_id=_col(r, "id"),
            opponent_tag=_col(r, "opponent_tag"),
            opponent_name=_col(r, "opponent_name"),
            opponent_th=_col(r, "opponent_th"),
            stars=_col(r, "stars"),
            destruction=_col(r, "destruction"),
            army_share_code=_col(r, "army_share_code"),
        )


def has_details(hits: Iterable[Hit]) -> bool:
    return any(h.detailed for h in hits)


@dataclass
class DayStats:
    player_tag: str
    attacks: list[int] = field(default_factory=list)  # trophy gains, chronological
    defenses: list[int] = field(default_factory=list)  # trophy losses (negative), chronological
    cap: int | None = BATTLES_PER_DAY  # daily attack/defense cap; None for tiers without one
    attack_hits: list[Hit] = field(default_factory=list)  # same battles as ``attacks``, with details
    defense_hits: list[Hit] = field(default_factory=list)

    def hits(self, kind: str) -> list[Hit]:
        """Battles of ``kind`` with whatever details are known (trophies only when built from
        bare trophy lists)."""
        values, hits = (
            (self.attacks, self.attack_hits) if kind == "attack" else (self.defenses, self.defense_hits)
        )
        if len(hits) == len(values):
            return hits
        return [Hit(kind, v) for v in values]

    @property
    def triples(self) -> int:
        return sum(1 for h in self.hits("attack") if h.triple)

    @property
    def detailed(self) -> bool:
        return has_details(self.hits("attack")) or has_details(self.hits("defense"))

    @property
    def offense(self) -> int:
        return sum(self.attacks)

    @property
    def defense(self) -> int:
        return sum(self.defenses)

    @property
    def net(self) -> int:
        return self.offense + self.defense

    @property
    def attacks_left(self) -> int | None:
        return None if self.cap is None else max(0, self.cap - len(self.attacks))

    @property
    def defenses_left(self) -> int | None:
        return None if self.cap is None else max(0, self.cap - len(self.defenses))


def aggregate_day(rows: Iterable[Any]) -> dict[str, DayStats]:
    """Group legend_events rows (ordered by occurred_at) into per-player DayStats."""
    out: dict[str, DayStats] = {}
    for r in rows:
        s = out.setdefault(r["player_tag"], DayStats(r["player_tag"]))
        hit = Hit.from_row(r)
        if hit.kind == "attack":
            s.attacks.append(hit.trophies)
            s.attack_hits.append(hit)
        else:
            s.defenses.append(hit.trophies)
            s.defense_hits.append(hit)
    return out


@dataclass
class SeasonDay:
    day: str
    start: int | None
    end: int | None
    stats: DayStats


@dataclass
class SeasonSummary:
    days: list[SeasonDay]

    @property
    def all_attacks(self) -> list[int]:
        return [a for d in self.days for a in d.stats.attacks]

    @property
    def all_defenses(self) -> list[int]:
        return [x for d in self.days for x in d.stats.defenses]

    @property
    def avg_offense(self) -> float:
        a = self.all_attacks
        return sum(a) / len(a) if a else 0.0

    @property
    def avg_defense(self) -> float:
        d = self.all_defenses
        return sum(d) / len(d) if d else 0.0

    @property
    def attack_hits(self) -> list[Hit]:
        return [h for d in self.days for h in d.stats.hits("attack")]

    @property
    def defense_hits(self) -> list[Hit]:
        return [h for d in self.days for h in d.stats.hits("defense")]

    @property
    def triple_rate(self) -> float:
        """Share of 3-star attacks. Attacks without star data (inferred from trophy changes) count
        as triples when they gained the max trophies."""
        a = self.attack_hits
        return sum(1 for h in a if h.triple) / len(a) if a else 0.0

    @property
    def estimated_triples(self) -> int:
        """Attacks in ``triple_rate`` judged by trophies because star data is missing."""
        return sum(1 for h in self.attack_hits if not h.detailed)

    @staticmethod
    def _avg_stars(hits: list[Hit]) -> float | None:
        known = [h.stars for h in hits if h.stars is not None]
        return sum(known) / len(known) if known else None

    @property
    def avg_attack_stars(self) -> float | None:
        return self._avg_stars(self.attack_hits)

    @property
    def avg_defense_stars(self) -> float | None:
        """Average stars opponents took on defense (battle-log data only)."""
        return self._avg_stars(self.defense_hits)

    @property
    def avg_defense_per_day(self) -> float:
        days = [d for d in self.days if d.stats.defenses]
        return sum(d.stats.defense for d in days) / len(days) if days else 0.0


def build_season(day_rows: Sequence[Any], event_rows: Sequence[Any]) -> SeasonSummary:
    """Combine legend_days rows and legend_events rows for one player into per-day records."""
    by_day: dict[str, list[Any]] = {}
    for e in event_rows:
        by_day.setdefault(e["legend_day"], []).append(e)
    starts = {r["legend_day"]: r for r in day_rows}
    days = sorted(set(by_day) | set(starts))
    out = []
    for d in days:
        evs = by_day.get(d, [])
        r = starts.get(d)
        tag = evs[0]["player_tag"] if evs else r["player_tag"]
        stats = aggregate_day(evs).get(tag, DayStats(tag))
        out.append(SeasonDay(d, r["start_trophies"] if r else None, r["end_trophies"] if r else None, stats))
    return SeasonSummary(out)


@dataclass
class BoardEntry:
    player_tag: str
    name: str
    town_hall: int | None
    trophies: int
    stats: DayStats


def sort_board(entries: Iterable[BoardEntry]) -> list[BoardEntry]:
    """Highest trophies first; ties broken by today's net, then name."""
    return sorted(entries, key=lambda e: (-e.trophies, -e.stats.net, e.name.lower()))


# ------------------------------------------------------------------ subscriptions


async def add_subscription(
    db: Database,
    *,
    guild_id: int | None,
    user_id: int,
    player_tag: str,
    channel_id: int = 0,
    attacks: bool = True,
    defenses: bool = True,
    ping_role_id: int | None = None,
    created_by: int,
) -> int:
    await db.execute(
        "INSERT INTO legend_subscriptions (guild_id, user_id, player_tag, channel_id, attacks, "
        "defenses, ping_role_id, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (player_tag, user_id, channel_id) DO UPDATE SET attacks = excluded.attacks, "
        "defenses = excluded.defenses, ping_role_id = excluded.ping_role_id",
        (guild_id, user_id, player_tag, channel_id, int(attacks), int(defenses), ping_role_id, created_by),
    )
    return await db.fetchval(
        "SELECT id FROM legend_subscriptions WHERE player_tag = ? AND user_id = ? AND channel_id = ?",
        (player_tag, user_id, channel_id),
    )


async def get_subscription(db: Database, sub_id: int) -> Row | None:
    return await db.fetchone("SELECT * FROM legend_subscriptions WHERE id = ?", (sub_id,))


async def update_subscription(db: Database, sub_id: int, **fields: Any) -> None:
    allowed = {"attacks", "defenses", "ping_role_id", "channel_id"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    await db.execute(f"UPDATE legend_subscriptions SET {sets} WHERE id = ?", (*fields.values(), sub_id))


async def delete_subscription(db: Database, sub_id: int) -> None:
    await db.execute("DELETE FROM legend_subscriptions WHERE id = ?", (sub_id,))


async def subscriptions_for_player(db: Database, player_tag: str) -> list[Row]:
    return await db.fetchall("SELECT * FROM legend_subscriptions WHERE player_tag = ?", (player_tag,))


async def list_subscriptions(
    db: Database, *, user_id: int | None = None, guild_id: int | None = None
) -> list[Row]:
    """Subscriptions owned by ``user_id`` and/or created in ``guild_id`` (OR'd together)."""
    conds, params = [], []
    if user_id is not None:
        conds.append("s.user_id = ?")
        params.append(user_id)
    if guild_id is not None:
        conds.append("s.guild_id = ?")
        params.append(guild_id)
    where = " OR ".join(conds) or "1"
    return await db.fetchall(
        "SELECT s.*, p.name FROM legend_subscriptions s "
        f"LEFT JOIN players p ON p.player_tag = s.player_tag WHERE {where} ORDER BY s.id",
        params,
    )


# ------------------------------------------------------------------ board players


async def board_add(db: Database, guild_id: int, tags: Iterable[str]) -> int:
    """Add tags, respecting BOARD_LIMIT. Returns how many were added."""
    existing = set(await board_tags(db, guild_id))
    room = BOARD_LIMIT - len(existing)
    new = [t for t in dict.fromkeys(tags) if t not in existing][: max(0, room)]
    await db.executemany(
        "INSERT OR IGNORE INTO legend_board_players (guild_id, player_tag) VALUES (?, ?)",
        [(guild_id, t) for t in new],
    )
    return len(new)


async def board_remove(db: Database, guild_id: int, tag: str) -> bool:
    n = await db.execute_rowcount(
        "DELETE FROM legend_board_players WHERE guild_id = ? AND player_tag = ?", (guild_id, tag)
    )
    return n > 0


async def board_clear(db: Database, guild_id: int) -> None:
    await db.execute("DELETE FROM legend_board_players WHERE guild_id = ?", (guild_id,))


async def board_tags(db: Database, guild_id: int) -> list[str]:
    rows = await db.fetchall(
        "SELECT player_tag FROM legend_board_players WHERE guild_id = ? ORDER BY added_at",
        (guild_id,),
    )
    return [r["player_tag"] for r in rows]


async def all_tracked_tags(db: Database) -> set[str]:
    rows = await db.fetchall(
        "SELECT player_tag FROM legend_subscriptions UNION SELECT player_tag FROM legend_board_players"
    )
    return {r["player_tag"] for r in rows}


# ------------------------------------------------------------------ event reads


async def day_events(db: Database, legend_day: str, tags: Sequence[str] | None = None) -> list[Row]:
    sql = "SELECT * FROM legend_events WHERE legend_day = ?"
    params: list[Any] = [legend_day]
    if tags is not None:
        if not tags:
            return []
        sql += f" AND player_tag IN ({','.join('?' * len(tags))})"
        params.extend(tags)
    return await db.fetchall(sql + " ORDER BY occurred_at, id", params)


async def get_battle(db: Database, event_id: int) -> Row | None:
    """A legend_events row with the tracked player's name (``player_name``), or None."""
    return await db.fetchone(
        "SELECT e.*, p.name AS player_name FROM legend_events e "
        "LEFT JOIN players p ON p.player_tag = e.player_tag WHERE e.id = ?",
        (event_id,),
    )


async def player_league(db: Database, player_tag: str) -> str | None:
    return await db.fetchval("SELECT league FROM players WHERE player_tag = ?", (player_tag,))


@dataclass
class WeekDay:
    day: str
    start: int | None
    end: int | None
    stats: DayStats


async def week_days(db: Database, player_tag: str, week_start: datetime) -> list[WeekDay]:
    """The 7 legend days of the ranked week starting at ``week_start`` (Monday 05:00 UTC)."""
    out = []
    for i in range(7):
        day = (week_start + timedelta(days=i)).date().isoformat()
        row = await legend_day_row(db, player_tag, day)
        out.append(
            WeekDay(
                day,
                row["start_trophies"] if row else None,
                row["end_trophies"] if row else None,
                await day_stats(db, player_tag, day),
            )
        )
    return out


async def day_stats(db: Database, player_tag: str, legend_day: str) -> DayStats:
    rows = await day_events(db, legend_day, [player_tag])
    stats = aggregate_day(rows).get(player_tag, DayStats(player_tag))
    stats.cap = daily_attack_cap(await player_league(db, player_tag))
    return stats


async def legend_day_row(db: Database, player_tag: str, legend_day: str) -> Row | None:
    return await db.fetchone(
        "SELECT * FROM legend_days WHERE player_tag = ? AND legend_day = ?", (player_tag, legend_day)
    )


async def season_summary(db: Database, player_tag: str, season_id: str) -> SeasonSummary:
    days = await db.fetchall(
        "SELECT * FROM legend_days WHERE player_tag = ? AND season_id = ? ORDER BY legend_day",
        (player_tag, season_id),
    )
    events = await db.fetchall(
        "SELECT * FROM legend_events WHERE player_tag = ? AND season_id = ? ORDER BY occurred_at, id",
        (player_tag, season_id),
    )
    return build_season(days, events)


async def player_seasons(db: Database, player_tag: str) -> list[Row]:
    """One row per season: final (latest day's end) trophies, days tracked, battles."""
    return await db.fetchall(
        "SELECT d.season_id, COUNT(*) AS days, "
        "(SELECT end_trophies FROM legend_days x WHERE x.player_tag = d.player_tag "
        " AND x.season_id = d.season_id ORDER BY legend_day DESC LIMIT 1) AS final_trophies, "
        "MAX(d.end_trophies) AS peak_trophies, "
        "(SELECT COUNT(*) FROM legend_events e WHERE e.player_tag = d.player_tag "
        " AND e.season_id = d.season_id AND e.kind = 'attack') AS attacks "
        "FROM legend_days d WHERE d.player_tag = ? GROUP BY d.season_id ORDER BY d.season_id DESC",
        (player_tag,),
    )


async def seasons_for_player(db: Database, player_tag: str) -> list[str]:
    rows = await db.fetchall(
        "SELECT DISTINCT season_id FROM legend_days WHERE player_tag = ? ORDER BY season_id DESC",
        (player_tag,),
    )
    return [r["season_id"] for r in rows]


async def board_entries(db: Database, tags: Sequence[str], legend_day: str) -> list[BoardEntry]:
    """BoardEntry for each tag we have player data for, sorted for display."""
    if not tags:
        return []
    q = ",".join("?" * len(tags))
    players = await db.fetchall(
        f"SELECT player_tag, name, town_hall, trophies, league FROM players WHERE player_tag IN ({q})",
        list(tags),
    )
    stats = aggregate_day(await day_events(db, legend_day, list(tags)))
    for p in players:
        stats.setdefault(p["player_tag"], DayStats(p["player_tag"])).cap = daily_attack_cap(p["league"])
    return sort_board(
        BoardEntry(
            p["player_tag"],
            p["name"],
            p["town_hall"],
            p["trophies"] or 0,
            stats.get(p["player_tag"], DayStats(p["player_tag"])),
        )
        for p in players
    )


async def legend_members(db: Database, clan_tags: Sequence[str]) -> list[str]:
    """Tags of players currently in ``clan_tags`` and in a tracked ranked tier (Dragon 28+)."""
    if not clan_tags:
        return []
    q = ",".join("?" * len(clan_tags))
    rows = await db.fetchall(
        f"SELECT player_tag, league FROM players WHERE clan_tag IN ({q})", tuple(clan_tags)
    )
    return [r["player_tag"] for r in rows if is_tracked_ranked(r["league"])]


async def top_today(
    db: Database, legend_day: str, mode: str, min_battles: int = 0, limit: int = 25
) -> list[Row]:
    """Best offense (most gained) or best defense (least lost) among tracked players today."""
    kind = "attack" if mode == "offense" else "defense"
    # Offense: most gained. Defense totals are <= 0, so "least lost" is also DESC.
    return await db.fetchall(
        "SELECT e.player_tag, p.name, p.trophies, SUM(e.trophies) AS total, COUNT(*) AS battles "
        "FROM legend_events e LEFT JOIN players p ON p.player_tag = e.player_tag "
        "WHERE e.legend_day = ? AND e.kind = ? GROUP BY e.player_tag HAVING COUNT(*) >= ? "
        "ORDER BY total DESC, battles DESC LIMIT ?",
        (legend_day, kind, min_battles, limit),
    )


# ------------------------------------------------------------------ daily summary bookkeeping


async def clans_with_daily_log(db: Database) -> list[str]:
    rows = await db.fetchall("SELECT DISTINCT clan_tag FROM clan_logs WHERE log_type = 'legend_daily'")
    return [r["clan_tag"] for r in rows]


async def daily_posted(db: Database, clan_tag: str, legend_day: str) -> bool:
    return bool(
        await db.fetchval(
            "SELECT 1 FROM legend_daily_posted WHERE clan_tag = ? AND legend_day = ?",
            (clan_tag, legend_day),
        )
    )


async def mark_daily_posted(db: Database, clan_tag: str, legend_day: str) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO legend_daily_posted (clan_tag, legend_day) VALUES (?, ?)",
        (clan_tag, legend_day),
    )


async def clan_day_rows(db: Database, clan_tag: str, legend_day: str) -> list[dict]:
    """Per-member day summary for a clan: start/end trophies, offense, defense, net.
    Members are players currently in the clan with a legend_days row for that day."""
    rows = await db.fetchall(
        "SELECT p.player_tag, p.name, d.start_trophies, d.end_trophies FROM players p "
        "JOIN legend_days d ON d.player_tag = p.player_tag AND d.legend_day = ? "
        "WHERE p.clan_tag = ?",
        (legend_day, clan_tag),
    )
    tags = [r["player_tag"] for r in rows]
    stats = aggregate_day(await day_events(db, legend_day, tags))
    out = []
    for r in rows:
        s = stats.get(r["player_tag"], DayStats(r["player_tag"]))
        out.append(
            dict(
                player_tag=r["player_tag"],
                name=r["name"],
                start=r["start_trophies"],
                end=r["end_trophies"],
                offense=s.offense,
                defense=s.defense,
                net=s.net,
                attacks=len(s.attacks),
                defenses=len(s.defenses),
                triples=s.triples if has_details(s.hits("attack")) else None,
            )
        )
    out.sort(key=lambda x: -(x["end"] or 0))
    return out
