"""Roster suggestions: a transparent 0-100 score per candidate from war history, activity and
Town Hall / hero strength.

The score is a weighted mix of six parts, each scaled to 0..1 (see ``WEIGHTS``):

- ``cwl``       CWL 3-star rate over the last N CWL seasons
- ``war``       3-star rate over regular and CWL wars in the last ``WAR_LOOKBACK_DAYS``
- ``stars``     average stars per attack over the same wars (every attack counts)
- ``hits``      attacks used out of attacks available in ended wars the player was in
- ``activity``  1 if seen within a day, falling linearly to 0 at ``INACTIVE_DAYS``
- ``strength``  Town Hall and combined hero levels relative to the candidate pool

3-star rates count attacks on an equal or higher Town Hall (dips say little) and use the Wilson
lower bound, so 3 of 3 doesn't outrank 40 of 50. Every history part is then blended toward the
pool's typical (median) value with weight ``PRIOR_SAMPLES / (n + PRIOR_SAMPLES)``: one bad
attack can't sink a player, and a player with no history at all sits in the middle of the pool
on those parts, so TH/heroes and activity decide their place. Friendly wars are ignored.

Everything except :func:`load_histories` is pure.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from clashhero.db import Database
from clashhero.repos.rosters import check_eligibility

WEIGHTS: dict[str, float] = {
    "cwl": 0.30,
    "war": 0.20,
    "stars": 0.10,
    "hits": 0.15,
    "activity": 0.10,
    "strength": 0.15,
}
PART_LABELS = {
    "cwl": "CWL 3★",
    "war": "war 3★",
    "stars": "avg stars",
    "hits": "attacks used",
    "activity": "activity",
    "strength": "TH/heroes",
}
HISTORY_PARTS = ("cwl", "war", "stars", "hits")
DEFAULT_CWL_SEASONS = 3
WAR_LOOKBACK_DAYS = 180
PRIOR_SAMPLES = 4
WILSON_Z = 1.28  # one-sided 90%
ACTIVE_DAYS = 1.0
INACTIVE_DAYS = 14.0
NEUTRAL = 0.5  # used when nobody in the pool has data for a part


# ------------------------------------------------------------------ data


@dataclass
class History:
    cwl_hits: int = 0  # CWL attacks on an equal or higher TH
    cwl_triples: int = 0
    war_hits: int = 0  # regular + CWL attacks on an equal or higher TH
    war_triples: int = 0
    attacks: int = 0  # every regular + CWL attack (for average stars)
    stars: int = 0
    used: int = 0  # attacks used in ended wars
    available: int = 0  # attacks available in those wars

    @property
    def avg_stars(self) -> float | None:
        return self.stars / self.attacks if self.attacks else None

    @property
    def empty(self) -> bool:
        return not (self.cwl_hits or self.war_hits or self.attacks or self.available)


@dataclass
class Candidate:
    tag: str
    name: str
    town_hall: int | None = None
    heroes: int | None = None
    last_seen: str | datetime | None = None
    user_id: int | None = None
    group: str | None = None  # current roster group when already signed up
    signed_up: bool = False

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Candidate:
        """From a :func:`clashhero.repos.rosters.suggestion_candidate_rows` row."""
        return cls(
            tag=row["player_tag"],
            name=row["name"] or row["player_tag"],
            town_hall=row["town_hall"],
            heroes=row["heroes_total"],
            last_seen=row["last_seen"],
            user_id=row["user_id"],
            group=row["group_name"],
            signed_up=bool(row["signed_up"]),
        )


@dataclass
class Scored:
    candidate: Candidate
    history: History
    parts: dict[str, float] = field(default_factory=dict)  # each 0..1
    seen_days: float | None = None

    @property
    def score(self) -> float:
        return 100 * sum(WEIGHTS[k] * self.parts.get(k, 0.0) for k in WEIGHTS)


# ------------------------------------------------------------------ math


def wilson_lower_bound(successes: int, n: int, z: float = WILSON_Z) -> float:
    """Lower bound of the Wilson score interval for a success proportion (0 when n is 0)."""
    if n <= 0:
        return 0.0
    p = successes / n
    z2 = z * z
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return max(0.0, (centre - margin) / (1 + z2 / n))


def shrink(value: float | None, n: int, neutral: float, k: float = PRIOR_SAMPLES) -> float:
    """Blend ``value`` (observed over ``n`` samples) toward ``neutral``; no samples -> neutral."""
    if value is None or n <= 0:
        return neutral
    w = n / (n + k)
    return w * value + (1 - w) * neutral


def _as_dt(value: str | datetime | None) -> datetime | None:
    if not value:
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def days_since(value: str | datetime | None, now: datetime) -> float | None:
    dt = _as_dt(value)
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds() / 86400)


def activity_score(days: float | None) -> float:
    """1 within ``ACTIVE_DAYS``, linear down to 0 at ``INACTIVE_DAYS``; unknown is neutral."""
    if days is None:
        return NEUTRAL
    if days <= ACTIVE_DAYS:
        return 1.0
    if days >= INACTIVE_DAYS:
        return 0.0
    return 1 - (days - ACTIVE_DAYS) / (INACTIVE_DAYS - ACTIVE_DAYS)


def strength_score(
    town_hall: int | None, heroes: int | None, th_range: tuple[int, int], max_heroes: int
) -> float:
    """Half Town Hall (min..max of the pool -> 0..1), half heroes (share of the pool's best)."""
    lo, hi = th_range
    th = town_hall or 0
    th_part = 1.0 if hi <= lo else min(1.0, max(0.0, (th - lo) / (hi - lo)))
    if not town_hall:
        th_part = 0.0
    hero_part = min(1.0, (heroes or 0) / max_heroes) if max_heroes else 1.0
    return 0.5 * th_part + 0.5 * hero_part


# ------------------------------------------------------------------ history


def _fair(attacker_th: int | None, defender_th: int | None) -> bool:
    """Attack on an equal or higher TH (unknown THs count)."""
    return not attacker_th or not defender_th or defender_th >= attacker_th


def build_histories(
    attacks: Iterable[Mapping[str, Any]],
    participations: Iterable[Mapping[str, Any]],
    *,
    cwl_seasons: Iterable[str],
    since: datetime,
) -> dict[str, History]:
    """Aggregate per-player history.

    attacks: our side's attacks with attacker_tag, stars, attacker_th, defender_th, war_type,
    cwl_season, preparation_start. participations: one per (player, ended war) with player_tag,
    war_type, cwl_season, preparation_start, attacks_per_member, used.
    CWL attacks count toward ``cwl`` when their season is in ``cwl_seasons``; regular and CWL
    wars prepared at or after ``since`` count toward ``war``, stars and hits."""
    seasons = set(cwl_seasons)
    out: dict[str, History] = {}

    def windows(row: Mapping[str, Any]) -> tuple[bool, bool]:
        if row["war_type"] == "friendly":
            return False, False
        in_cwl = row["war_type"] == "cwl" and row["cwl_season"] in seasons
        prep = _as_dt(row["preparation_start"])
        return in_cwl, prep is not None and prep >= since

    for a in attacks:
        in_cwl, in_war = windows(a)
        if not (in_cwl or in_war):
            continue
        h = out.setdefault(a["attacker_tag"], History())
        fair = _fair(a["attacker_th"], a["defender_th"])
        triple = a["stars"] == 3
        if in_cwl and fair:
            h.cwl_hits += 1
            h.cwl_triples += triple
        if in_war:
            h.attacks += 1
            h.stars += a["stars"]
            if fair:
                h.war_hits += 1
                h.war_triples += triple
    for p in participations:
        in_cwl, in_war = windows(p)
        if not (in_cwl or in_war):
            continue
        allowed = p["attacks_per_member"] or (1 if p["war_type"] == "cwl" else 2)
        h = out.setdefault(p["player_tag"], History())
        h.available += allowed
        h.used += min(allowed, p["used"] or 0)
    return out


def _raw_parts(h: History) -> dict[str, tuple[float | None, int]]:
    """Part -> (observed value or None, sample size)."""
    avg = h.avg_stars
    return {
        "cwl": (wilson_lower_bound(h.cwl_triples, h.cwl_hits) if h.cwl_hits else None, h.cwl_hits),
        "war": (wilson_lower_bound(h.war_triples, h.war_hits) if h.war_hits else None, h.war_hits),
        "stars": (avg / 3 if avg is not None else None, h.attacks),
        "hits": (h.used / h.available if h.available else None, h.available),
    }


# ------------------------------------------------------------------ scoring


def score_candidates(
    candidates: Sequence[Candidate], histories: Mapping[str, History], now: datetime
) -> list[Scored]:
    """Score and rank candidates, best first (ties: higher TH, more heroes, name)."""
    raws = {c.tag: _raw_parts(histories.get(c.tag) or History()) for c in candidates}
    neutral = {}
    for part in HISTORY_PARTS:
        # typical value among players with a real sample (tiny samples skew the median)
        observed = [raws[c.tag][part] for c in candidates if raws[c.tag][part][0] is not None]
        solid = [v for v, n in observed if n >= PRIOR_SAMPLES] or [v for v, _ in observed]
        neutral[part] = median(solid) if solid else NEUTRAL
    ths = [c.town_hall for c in candidates if c.town_hall]
    th_range = (min(ths), max(ths)) if ths else (0, 0)
    max_heroes = max((c.heroes or 0 for c in candidates), default=0)

    out = []
    for c in candidates:
        parts = {p: shrink(v, n, neutral[p]) for p, (v, n) in raws[c.tag].items()}
        days = days_since(c.last_seen, now)
        parts["activity"] = activity_score(days)
        parts["strength"] = strength_score(c.town_hall, c.heroes, th_range, max_heroes)
        out.append(Scored(c, histories.get(c.tag) or History(), parts, days))
    out.sort(
        key=lambda s: (
            -round(s.score, 6),
            -(s.candidate.town_hall or 0),
            -(s.candidate.heroes or 0),
            s.candidate.name.lower(),
        )
    )
    return out


def split_eligible(
    roster: Mapping[str, Any],
    candidates: Iterable[Candidate],
    *,
    linked: Iterable[str],
    other_rosters: Mapping[str, str],
) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
    """(eligible, [(excluded, reason)]) under the roster's rules: TH/hero limits, linked
    accounts (unless unlinked are allowed) and one roster per category (unless allowed)."""
    linked = set(linked)
    ok: list[Candidate] = []
    excluded: list[tuple[Candidate, str]] = []
    for c in candidates:
        reason = check_eligibility(roster, c.town_hall, c.heroes)
        if reason is None and not roster["allow_unlinked"] and c.tag not in linked:
            reason = "not linked to a Discord user"
        if reason is None and not roster["allow_multi_signup"] and c.tag in other_rosters:
            reason = f"already on {other_rosters[c.tag]}"
        if reason is None:
            ok.append(c)
        else:
            excluded.append((c, reason))
    return ok, excluded


def pick(scored: Sequence[Scored], size: int, substitutes: int) -> tuple[list[Scored], list[Scored]]:
    """(top ``size``, next ``substitutes``)."""
    return list(scored[:size]), list(scored[size : size + substitutes])


def describe_weights() -> str:
    return " · ".join(f"{PART_LABELS[k]} {round(w * 100)}%" for k, w in WEIGHTS.items())


# ------------------------------------------------------------------ queries


async def recent_cwl_seasons(db: Database, count: int) -> list[str]:
    """The ``count`` most recent CWL seasons with recorded wars, newest first."""
    rows = await db.fetchall(
        "SELECT DISTINCT cwl_season FROM wars WHERE war_type = 'cwl' AND cwl_season IS NOT NULL "
        "ORDER BY cwl_season DESC LIMIT ?",
        (count,),
    )
    return [r["cwl_season"] for r in rows]


def _chunks(items: Sequence[str], size: int = 400) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def load_histories(
    db: Database,
    tags: Iterable[str],
    *,
    cwl_seasons: int = DEFAULT_CWL_SEASONS,
    now: datetime | None = None,
    lookback_days: int = WAR_LOOKBACK_DAYS,
) -> tuple[dict[str, History], list[str]]:
    """(tag -> History, CWL seasons used) for the given players, from recorded wars."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=lookback_days)
    seasons = await recent_cwl_seasons(db, cwl_seasons)
    tags = sorted(set(tags))
    attacks: list[Any] = []
    participations: list[Any] = []
    season_clause = (
        f" OR (w.war_type = 'cwl' AND w.cwl_season IN ({', '.join('?' * len(seasons))}))" if seasons else ""
    )
    for chunk in _chunks(tags):
        marks = ", ".join("?" * len(chunk))
        window = f"(w.preparation_start >= ?{season_clause})"
        params = [*chunk, since.isoformat(), *seasons]
        attacks += await db.fetchall(
            "SELECT a.attacker_tag, a.stars, a.attacker_th, a.defender_th, w.war_type, "
            "w.cwl_season, w.preparation_start FROM war_attacks a JOIN wars w ON w.id = a.war_id "
            f"WHERE a.is_opponent = 0 AND w.war_type != 'friendly' AND a.attacker_tag IN ({marks}) "
            f"AND {window}",
            params,
        )
        participations += await db.fetchall(
            "SELECT m.player_tag, w.war_type, w.cwl_season, w.preparation_start, "
            "w.attacks_per_member, (SELECT COUNT(*) FROM war_attacks a WHERE a.war_id = w.id "
            " AND a.attacker_tag = m.player_tag AND a.is_opponent = 0) AS used "
            "FROM war_members m JOIN wars w ON w.id = m.war_id "
            "WHERE m.is_opponent = 0 AND w.state = 'warEnded' AND w.war_type != 'friendly' "
            f"AND m.player_tag IN ({marks}) AND {window}",
            params,
        )
    histories = build_histories(attacks, participations, cwl_seasons=seasons, since=since)
    return histories, seasons
