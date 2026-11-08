"""War / CWL statistics over the collector's wars, war_members and war_attacks tables.

Query functions return plain dicts; the ``summarize_*`` / ``*_standings`` helpers are pure so
they can be unit tested without a database.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from clashhero.db import Database

WAR_TYPES = ("random", "friendly", "cwl")


# ------------------------------------------------------------------ pure calculators


def th_bucket(attacker_th: int | None, defender_th: int | None) -> str:
    """'up' (hit a higher TH), 'even', or 'down'. Unknown THs count as even."""
    if not attacker_th or not defender_th or attacker_th == defender_th:
        return "even"
    return "up" if defender_th > attacker_th else "down"


@dataclass
class AttackSummary:
    attacks: int = 0
    stars: int = 0
    triples: int = 0
    zeros: int = 0
    destruction: float = 0.0
    fresh: int = 0
    fresh_triples: int = 0
    missed: int = 0
    town_hall: int | None = None  # attacker's TH in their latest attack
    buckets: dict[str, list[int]] = field(
        default_factory=lambda: {"up": [0, 0], "even": [0, 0], "down": [0, 0]}
    )  # bucket -> [attacks, triples]

    @property
    def avg_stars(self) -> float:
        return self.stars / self.attacks if self.attacks else 0.0

    @property
    def avg_destruction(self) -> float:
        return self.destruction / self.attacks if self.attacks else 0.0

    @property
    def triple_rate(self) -> float:
        return self.triples / self.attacks if self.attacks else 0.0

    @property
    def fresh_triple_rate(self) -> float:
        return self.fresh_triples / self.fresh if self.fresh else 0.0

    @property
    def possible(self) -> int:
        return self.attacks + self.missed

    def bucket_rate(self, bucket: str) -> tuple[int, float]:
        n, t = self.buckets[bucket]
        return n, (t / n if n else 0.0)


def summarize_attacks(rows: Iterable[Any], missed: int = 0) -> AttackSummary:
    """Rows need stars, destruction, is_fresh, attacker_th, defender_th (oldest first)."""
    s = AttackSummary(missed=missed)
    for r in rows:
        s.attacks += 1
        s.stars += r["stars"]
        s.destruction += r["destruction"]
        triple = r["stars"] == 3
        s.triples += triple
        s.zeros += r["stars"] == 0
        if r["is_fresh"]:
            s.fresh += 1
            s.fresh_triples += triple
        s.town_hall = r["attacker_th"] or s.town_hall
        b = s.buckets[th_bucket(r["attacker_th"], r["defender_th"])]
        b[0] += 1
        b[1] += triple
    return s


@dataclass
class DefenseSummary:
    defenses: int = 0
    triples_allowed: int = 0
    stars_allowed: int = 0
    destruction: float = 0.0
    town_hall: int | None = None  # defender's TH in their latest defense

    @property
    def hold_rate(self) -> float:
        """Share of defenses that did NOT give up three stars."""
        return 1 - self.triples_allowed / self.defenses if self.defenses else 0.0

    @property
    def avg_stars(self) -> float:
        return self.stars_allowed / self.defenses if self.defenses else 0.0

    @property
    def avg_destruction(self) -> float:
        return self.destruction / self.defenses if self.defenses else 0.0


def summarize_defenses(rows: Iterable[dict]) -> DefenseSummary:
    """Rows need stars, destruction and optionally defender_th (oldest first)."""
    s = DefenseSummary()
    for r in rows:
        s.defenses += 1
        s.stars_allowed += r["stars"]
        s.destruction += r["destruction"]
        s.triples_allowed += r["stars"] == 3
        s.town_hall = r.get("defender_th") or s.town_hall
    return s


def war_result(stars: int, opp_stars: int, destruction: float, opp_destruction: float) -> str:
    if stars != opp_stars:
        return "win" if stars > opp_stars else "lose"
    if destruction != opp_destruction:
        return "win" if destruction > opp_destruction else "lose"
    return "tie"


@dataclass
class Standing:
    tag: str
    name: str
    stars: int = 0
    destruction: float = 0.0  # summed destruction % across wars x team size (in-game tiebreak)
    wins: int = 0
    losses: int = 0
    ties: int = 0
    attacks: int = 0

    @property
    def total_stars(self) -> int:
        return self.stars + 10 * self.wins


def cwl_standings(wars: Iterable[dict]) -> list[Standing]:
    """Group standings from CWL wars. Each war dict: clan_tag, clan_name, clan_stars,
    clan_destruction, clan_attacks, opp_tag, opp_name, opp_stars, opp_destruction, opp_attacks,
    team_size, state. Winners of *ended* wars get +10 stars. Ordered by total stars then
    destruction, as in game."""
    table: dict[str, Standing] = {}

    def get(tag: str, name: str) -> Standing:
        if tag not in table:
            table[tag] = Standing(tag, name)
        return table[tag]

    for w in wars:
        if w.get("state") in (None, "notInWar", "preparation"):
            for t, n in ((w["clan_tag"], w["clan_name"]), (w["opp_tag"], w["opp_name"])):
                get(t, n)
            continue
        size = w.get("team_size") or 0
        a, b = get(w["clan_tag"], w["clan_name"]), get(w["opp_tag"], w["opp_name"])
        a.stars += w["clan_stars"]
        b.stars += w["opp_stars"]
        a.destruction += w["clan_destruction"] * size
        b.destruction += w["opp_destruction"] * size
        a.attacks += w.get("clan_attacks", 0) or 0
        b.attacks += w.get("opp_attacks", 0) or 0
        if w["state"] == "warEnded":
            res = war_result(w["clan_stars"], w["opp_stars"], w["clan_destruction"], w["opp_destruction"])
            if res == "win":
                a.wins += 1
                b.losses += 1
            elif res == "lose":
                b.wins += 1
                a.losses += 1
            else:
                a.ties += 1
                b.ties += 1
    return sorted(table.values(), key=lambda s: (-s.total_stars, -s.destruction))


def th_spread(town_halls: Iterable[int]) -> dict[int, int]:
    out: dict[int, int] = defaultdict(int)
    for th in town_halls:
        out[th] += 1
    return dict(sorted(out.items(), reverse=True))


def remaining_attacks(
    members: Sequence[dict], attacks: Sequence[dict], attacks_per_member: int
) -> list[tuple[dict, int]]:
    """(member, attacks left) for our members who haven't used all attacks, by map position.
    members: dicts with tag/name/map_position; attacks: dicts with attacker_tag."""
    used: dict[str, int] = defaultdict(int)
    for a in attacks:
        used[a["attacker_tag"]] += 1
    out = [(m, attacks_per_member - used[m["tag"]]) for m in members]
    return sorted([(m, left) for m, left in out if left > 0], key=lambda x: x[0].get("map_position") or 0)


# ------------------------------------------------------------------ war queries


def _type_clause(war_type: str | None) -> tuple[str, list]:
    if not war_type or war_type == "all":
        return "", []
    return " AND w.war_type = ?", [war_type]


async def current_war(db: Database, clan_tag: str) -> dict | None:
    """The clan's most recent war (in preparation, in war, or most recently ended)."""
    row = await db.fetchone(
        "SELECT * FROM wars WHERE clan_tag = ? ORDER BY preparation_start DESC LIMIT 1", (clan_tag,)
    )
    return dict(row) if row else None


async def war_members(db: Database, war_id: int, *, opponent: bool) -> list[dict]:
    rows = await db.fetchall(
        "SELECT * FROM war_members WHERE war_id = ? AND is_opponent = ? ORDER BY map_position",
        (war_id, int(opponent)),
    )
    return [dict(r) for r in rows]


async def war_attacks(db: Database, war_id: int) -> list[dict]:
    rows = await db.fetchall("SELECT * FROM war_attacks WHERE war_id = ? ORDER BY attack_order", (war_id,))
    return [dict(r) for r in rows]


async def missed_attacks(db: Database, war_id: int) -> list[dict]:
    """Our members with unused attacks in a war: dicts with player_tag, name, used, allowed."""
    war = await db.fetchone("SELECT attacks_per_member FROM wars WHERE id = ?", (war_id,))
    if war is None:
        return []
    allowed = war["attacks_per_member"] or 2
    rows = await db.fetchall(
        "SELECT m.player_tag, m.name, m.map_position, m.town_hall, "
        "(SELECT COUNT(*) FROM war_attacks a WHERE a.war_id = m.war_id "
        " AND a.attacker_tag = m.player_tag) AS used "
        "FROM war_members m WHERE m.war_id = ? AND m.is_opponent = 0 ORDER BY m.map_position",
        (war_id,),
    )
    return [{**dict(r), "allowed": allowed} for r in rows if r["used"] < allowed]


async def _attack_rows(
    db: Database,
    *,
    clan_tags: Sequence[str] | None = None,
    attacker_tags: Sequence[str] | None = None,
    defender_tags: Sequence[str] | None = None,
    opponent: bool,
    since: datetime | None = None,
    until: datetime | None = None,
    war_type: str | None = None,
    town_hall: int | None = None,
) -> list[dict]:
    sql = (
        "SELECT a.*, w.clan_tag, w.war_type, w.cwl_season, w.cwl_round, w.preparation_start, "
        "w.opponent_name FROM war_attacks a JOIN wars w ON w.id = a.war_id "
        "WHERE a.is_opponent = ?"
    )
    params: list[Any] = [int(opponent)]
    for col, values in (
        ("w.clan_tag", clan_tags),
        ("a.attacker_tag", attacker_tags),
        ("a.defender_tag", defender_tags),
    ):
        if values is not None:
            if not values:
                return []
            sql += f" AND {col} IN ({','.join('?' * len(values))})"
            params += list(values)
    if since:
        sql += " AND w.preparation_start >= ?"
        params.append(since.isoformat())
    if until:
        sql += " AND w.preparation_start < ?"
        params.append(until.isoformat())
    clause, extra = _type_clause(war_type)
    sql += clause
    params += extra
    if town_hall:
        sql += " AND " + ("a.defender_th" if opponent else "a.attacker_th") + " = ?"
        params.append(town_hall)
    return [dict(r) for r in await db.fetchall(sql + " ORDER BY w.preparation_start, a.attack_order", params)]


async def _missed_counts(
    db: Database,
    *,
    clan_tags: Sequence[str] | None = None,
    player_tags: Sequence[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    war_type: str | None = None,
) -> dict[str, int]:
    """player_tag -> missed attacks across ended wars."""
    sql = (
        "SELECT m.player_tag, w.attacks_per_member AS allowed, "
        "(SELECT COUNT(*) FROM war_attacks a WHERE a.war_id = w.id "
        " AND a.attacker_tag = m.player_tag) AS used "
        "FROM war_members m JOIN wars w ON w.id = m.war_id "
        "WHERE m.is_opponent = 0 AND w.state = 'warEnded'"
    )
    params: list[Any] = []
    for col, values in (("w.clan_tag", clan_tags), ("m.player_tag", player_tags)):
        if values is not None:
            if not values:
                return {}
            sql += f" AND {col} IN ({','.join('?' * len(values))})"
            params += list(values)
    if since:
        sql += " AND w.preparation_start >= ?"
        params.append(since.isoformat())
    if until:
        sql += " AND w.preparation_start < ?"
        params.append(until.isoformat())
    clause, extra = _type_clause(war_type)
    sql += clause
    params += extra
    out: dict[str, int] = defaultdict(int)
    for r in await db.fetchall(sql, params):
        out[r["player_tag"]] += max(0, (r["allowed"] or 2) - r["used"])
    return dict(out)


async def player_names(db: Database, tags: Iterable[str]) -> dict[str, str]:
    tags = list(tags)
    if not tags:
        return {}
    rows = await db.fetchall(
        f"SELECT player_tag, name FROM players WHERE player_tag IN ({','.join('?' * len(tags))})", tags
    )
    names = {r["player_tag"]: r["name"] for r in rows}
    missing = [t for t in tags if t not in names]
    if missing:
        rows = await db.fetchall(
            "SELECT player_tag, name FROM war_members WHERE player_tag IN "
            f"({','.join('?' * len(missing))}) GROUP BY player_tag",
            missing,
        )
        names.update({r["player_tag"]: r["name"] for r in rows})
    return names


async def count_wars(
    db: Database,
    clan_tags: Sequence[str],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    war_type: str | None = None,
) -> int:
    """Wars our clans fought (battle day started) in the period, for stats footers."""
    if not clan_tags:
        return 0
    sql = (
        "SELECT COUNT(*) FROM wars w WHERE w.state != 'preparation' "
        f"AND w.clan_tag IN ({','.join('?' * len(clan_tags))})"
    )
    params: list[Any] = list(clan_tags)
    if since:
        sql += " AND w.preparation_start >= ?"
        params.append(since.isoformat())
    if until:
        sql += " AND w.preparation_start < ?"
        params.append(until.isoformat())
    clause, extra = _type_clause(war_type)
    return await db.fetchval(sql + clause, params + extra, 0)


async def attack_leaderboard(
    db: Database,
    clan_tags: Sequence[str],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    war_type: str | None = None,
    town_hall: int | None = None,
) -> list[tuple[str, AttackSummary]]:
    """Per-attacker summaries for our clans' attacks, best triple rate first."""
    rows = await _attack_rows(
        db,
        clan_tags=clan_tags,
        opponent=False,
        since=since,
        until=until,
        war_type=war_type,
        town_hall=town_hall,
    )
    by_player: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_player[r["attacker_tag"]].append(r)
    missed = await _missed_counts(db, clan_tags=clan_tags, since=since, until=until, war_type=war_type)
    tags = set(by_player) | ({t for t, n in missed.items() if n} if not town_hall else set())
    out = [(t, summarize_attacks(by_player.get(t, []), missed.get(t, 0))) for t in tags]
    return sorted(out, key=lambda x: (-x[1].triple_rate, -x[1].attacks, x[1].missed))


async def defense_leaderboard(
    db: Database,
    clan_tags: Sequence[str],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    war_type: str | None = None,
    town_hall: int | None = None,
) -> list[tuple[str, DefenseSummary]]:
    """Per-defender summaries for our members (attacks made by opponents), best hold first."""
    rows = await _attack_rows(
        db,
        clan_tags=clan_tags,
        opponent=True,
        since=since,
        until=until,
        war_type=war_type,
        town_hall=town_hall,
    )
    by_player: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_player[r["defender_tag"]].append(r)
    out = [(t, summarize_defenses(rs)) for t, rs in by_player.items()]
    return sorted(out, key=lambda x: (-x[1].hold_rate, -x[1].defenses))


@dataclass
class PlayerWarHistory:
    wars: int
    offense: AttackSummary
    defense: DefenseSummary
    recent: list[dict]


async def player_war_history(
    db: Database,
    player_tags: Sequence[str],
    *,
    since: datetime | None = None,
    war_type: str | None = None,
    recent_limit: int = 10,
) -> PlayerWarHistory:
    attacks = await _attack_rows(
        db, attacker_tags=player_tags, opponent=False, since=since, war_type=war_type
    )
    defenses = await _attack_rows(
        db, defender_tags=player_tags, opponent=True, since=since, war_type=war_type
    )
    missed = await _missed_counts(db, player_tags=player_tags, since=since, war_type=war_type)
    sql = (
        "SELECT COUNT(DISTINCT m.war_id) FROM war_members m JOIN wars w ON w.id = m.war_id "
        f"WHERE m.is_opponent = 0 AND m.player_tag IN ({','.join('?' * len(player_tags))})"
    )
    params: list[Any] = list(player_tags)
    if since:
        sql += " AND w.preparation_start >= ?"
        params.append(since.isoformat())
    clause, extra = _type_clause(war_type)
    wars = await db.fetchval(sql + clause, params + extra, 0) if player_tags else 0
    return PlayerWarHistory(
        wars=wars,
        offense=summarize_attacks(attacks, sum(missed.values())),
        defense=summarize_defenses(defenses),
        recent=list(reversed(attacks))[:recent_limit],
    )


# ------------------------------------------------------------------ CWL queries


async def cwl_wars(db: Database, clan_tag: str, season: str) -> list[dict]:
    rows = await db.fetchall(
        "SELECT * FROM wars WHERE clan_tag = ? AND war_type = 'cwl' AND cwl_season = ? "
        "ORDER BY preparation_start",
        (clan_tag, season),
    )
    return [dict(r) for r in rows]


@dataclass
class CWLMemberStats:
    tag: str
    name: str
    town_hall: int | None = None
    rounds: int = 0  # rounds in the lineup (battle day started)
    attacks: int = 0
    missed: int = 0
    stars: int = 0
    new_stars: int = 0
    destruction: float = 0.0
    triples: int = 0
    defenses: int = 0
    stars_allowed: int = 0

    @property
    def avg_destruction(self) -> float:
        return self.destruction / self.attacks if self.attacks else 0.0


async def cwl_member_stats(db: Database, clan_tag: str, season: str) -> list[CWLMemberStats]:
    """Season stats per member across the clan's CWL wars, by stars then destruction."""
    wars = [w for w in await cwl_wars(db, clan_tag, season) if w["state"] != "preparation"]
    stats: dict[str, CWLMemberStats] = {}
    for w in wars:
        members = await war_members(db, w["id"], opponent=False)
        attacks = await war_attacks(db, w["id"])
        for m in members:
            s = stats.setdefault(m["player_tag"], CWLMemberStats(m["player_tag"], m["name"]))
            s.name, s.town_hall = m["name"], m["town_hall"]
            s.rounds += 1
            made = [a for a in attacks if a["attacker_tag"] == m["player_tag"] and not a["is_opponent"]]
            for a in made:
                s.attacks += 1
                s.stars += a["stars"]
                s.new_stars += a["new_stars"]
                s.destruction += a["destruction"]
                s.triples += a["stars"] == 3
            if w["state"] == "warEnded" and not made:
                s.missed += 1
            for a in attacks:
                if a["is_opponent"] and a["defender_tag"] == m["player_tag"]:
                    s.defenses += 1
                    s.stars_allowed += a["stars"]
    return sorted(stats.values(), key=lambda s: (-s.stars, -s.destruction, s.missed))


async def player_cwl_history(
    db: Database, player_tags: Sequence[str], season: str | None = None
) -> list[dict]:
    """One row per CWL war the player was in, newest first: season, round, clan name, opponent,
    the attack (with ``target_pos``, the enemy base's map position) and the best defense."""
    if not player_tags:
        return []
    q = ",".join("?" * len(player_tags))
    sql = (
        "SELECT w.id AS war_id, w.cwl_season, w.cwl_round, w.opponent_name, w.state, w.clan_tag, "
        "c.name AS clan_name, m.player_tag, m.name, m.town_hall, m.map_position FROM war_members m "
        "JOIN wars w ON w.id = m.war_id LEFT JOIN clans c ON c.clan_tag = w.clan_tag "
        f"WHERE w.war_type = 'cwl' AND m.is_opponent = 0 AND m.player_tag IN ({q})"
    )
    params: list[Any] = list(player_tags)
    if season:
        sql += " AND w.cwl_season = ?"
        params.append(season)
    out = []
    for r in await db.fetchall(sql + " ORDER BY w.preparation_start DESC", params):
        att = await db.fetchone(
            "SELECT a.*, COALESCE(a.defender_pos, (SELECT d.map_position FROM war_members d "
            " WHERE d.war_id = a.war_id AND d.player_tag = a.defender_tag)) AS target_pos "
            "FROM war_attacks a WHERE a.war_id = ? AND a.attacker_tag = ? AND a.is_opponent = 0",
            (r["war_id"], r["player_tag"]),
        )
        dfn = await db.fetchone(
            "SELECT * FROM war_attacks WHERE war_id = ? AND defender_tag = ? AND is_opponent = 1 "
            "ORDER BY stars DESC, destruction DESC LIMIT 1",
            (r["war_id"], r["player_tag"]),
        )
        out.append({**dict(r), "attack": dict(att) if att else None, "defense": dict(dfn) if dfn else None})
    return out


# ------------------------------------------------------------------ callers


async def set_caller(
    db: Database,
    guild_id: int,
    war_id: int,
    position: int,
    *,
    player_tag: str | None,
    user_id: int | None,
    note: str | None,
    assigned_by: int,
) -> None:
    await db.execute(
        "INSERT INTO war_calls (guild_id, war_id, position, player_tag, user_id, note, assigned_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (guild_id, war_id, position) DO UPDATE SET "
        "player_tag = excluded.player_tag, user_id = excluded.user_id, note = excluded.note, "
        "assigned_by = excluded.assigned_by, assigned_at = datetime('now')",
        (guild_id, war_id, position, player_tag, user_id, note, assigned_by),
    )


async def clear_caller(db: Database, guild_id: int, war_id: int, position: int | None = None) -> int:
    if position is None:
        return await db.execute_rowcount(
            "DELETE FROM war_calls WHERE guild_id = ? AND war_id = ?", (guild_id, war_id)
        )
    return await db.execute_rowcount(
        "DELETE FROM war_calls WHERE guild_id = ? AND war_id = ? AND position = ?",
        (guild_id, war_id, position),
    )


async def get_callers(db: Database, guild_id: int, war_id: int) -> list[dict]:
    """This server's calls for a war. Other servers tracking the same clan have their own."""
    rows = await db.fetchall(
        "SELECT * FROM war_calls WHERE guild_id = ? AND war_id = ? ORDER BY position", (guild_id, war_id)
    )
    return [dict(r) for r in rows]
