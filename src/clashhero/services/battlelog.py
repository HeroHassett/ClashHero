"""Player battle log (``GET /players/{tag}/battlelog``): parsing and bookkeeping.

The endpoint returns a player's most recent battles (about 40), newest first, with opponent,
stars, destruction, loot and the attacking army's share code. coc.py 4.0 doesn't wrap it, so the
collector calls it directly.

``battleType`` values seen: ``legend`` (Legend I), ``ranked`` (other ranked tiers) and
``homeVillage`` (regular multiplayer). It does not include the trophy change per battle, so the
collector splits the trophy delta it observes between polls across the new ranked battles.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)

RANKED_TYPES = frozenset({"legend", "ranked"})
KNOWN_TYPES = RANKED_TYPES | {"homeVillage"}
# If trophies move but no ranked battle shows up within this window (e.g. a new battle type after
# a game update), the collector falls back to inferring the battle from the trophy change.
PENDING_TIMEOUT = timedelta(minutes=30)

_warned_types: set[str] = set()


def note_unknown_types(battles: Iterable[Battle]) -> set[str]:
    """Log each battle type this version doesn't recognise, once. Returns the new ones."""
    new = {b.battle_type for b in battles if b.battle_type not in KNOWN_TYPES} - _warned_types
    for t in sorted(new):
        log.warning("Unknown battle log type %r; it isn't treated as ranked. See README 'Game updates'.", t)
    _warned_types.update(new)
    return new


@dataclass(frozen=True)
class Battle:
    timestamp: str  # API format, e.g. "20261104T020237.000Z" (sorts chronologically)
    battle_type: str
    attack: bool
    opponent_tag: str
    opponent_name: str
    opponent_th: int | None
    stars: int
    destruction: int
    army_share_code: str | None

    @property
    def key(self) -> str:
        return f"{self.timestamp}|{'A' if self.attack else 'D'}|{self.opponent_tag}"

    @property
    def ranked(self) -> bool:
        return self.battle_type in RANKED_TYPES

    @property
    def kind(self) -> str:
        return "attack" if self.attack else "defense"

    @property
    def occurred_at(self) -> datetime:
        return parse_timestamp(self.timestamp)


def parse_timestamp(raw: str) -> datetime:
    return datetime.strptime(raw, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=UTC)


def parse_battles(items: Iterable[dict[str, Any]]) -> list[Battle]:
    out = []
    for it in items or ():
        ts = it.get("battleTimestamp")
        if not ts:
            continue
        out.append(
            Battle(
                timestamp=ts,
                battle_type=str(it.get("battleType") or ""),
                attack=bool(it.get("attack")),
                opponent_tag=it.get("opponentPlayerTag") or "",
                opponent_name=it.get("opponentName") or "?",
                opponent_th=it.get("opponentTownHallLevel"),
                stars=int(it.get("stars") or 0),
                destruction=int(it.get("destructionPercentage") or 0),
                army_share_code=it.get("armyShareCode"),
            )
        )
    return out


@dataclass
class Cursor:
    """What we've already processed for a player: newest timestamp seen, the battle keys at that
    timestamp, and trophy change observed while no new battle was visible yet."""

    timestamp: str | None = None
    keys: list[str] = field(default_factory=list)
    pending_delta: int = 0
    pending_since: str | None = None  # ISO time the pending change was first seen

    def to_json(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "keys": self.keys,
            "pending_delta": self.pending_delta,
            "pending_since": self.pending_since,
        }

    @classmethod
    def from_json(cls, d: dict | None) -> Cursor:
        d = d or {}
        return cls(
            d.get("timestamp"),
            list(d.get("keys") or []),
            int(d.get("pending_delta") or 0),
            d.get("pending_since"),
        )


def take_new(battles: Sequence[Battle], cursor: Cursor | None) -> tuple[list[Battle], Cursor]:
    """Battles newer than ``cursor``, oldest first, plus the advanced cursor.

    With no cursor (first look at this player) everything is treated as already seen, so a
    newly tracked player doesn't flood alerts with old battles.
    """
    if not battles:
        return [], cursor or Cursor()
    newest = max(b.timestamp for b in battles)
    keys_at_newest = sorted(b.key for b in battles if b.timestamp == newest)
    if cursor is None or cursor.timestamp is None:
        return [], Cursor(newest, keys_at_newest, cursor.pending_delta if cursor else 0)
    fresh = [
        b
        for b in battles
        if b.timestamp > cursor.timestamp or (b.timestamp == cursor.timestamp and b.key not in cursor.keys)
    ]
    fresh.sort(key=lambda b: b.timestamp)
    if newest < cursor.timestamp:  # stale response; keep our cursor
        return fresh, cursor
    if newest == cursor.timestamp:
        keys_at_newest = sorted(set(cursor.keys) | set(keys_at_newest))
    return fresh, Cursor(newest, keys_at_newest, cursor.pending_delta, cursor.pending_since)


def _even_split(total: int, n: int) -> list[int]:
    if n <= 0:
        return []
    sign = 1 if total >= 0 else -1
    base, rem = divmod(abs(total), n)
    return [sign * (base + (1 if i < rem else 0)) for i in range(n)]


def assign_trophies(battles: Sequence[Battle], delta: int, *, defenses_gain: bool = False) -> list[int]:
    """Split an observed trophy change across ``battles`` (ranked, oldest first).

    In Legend I attacks gain and defenses lose, so gains go to attacks and losses to defenses.
    Below Legend I both attacks and defenses gain, so a gain is shared across all of them.
    When several battles land between two polls only the net change is known, so the split is
    approximate.
    """
    if defenses_gain:
        return _even_split(max(delta, 0), len(battles)) if battles else []
    att = [i for i, b in enumerate(battles) if b.attack]
    dfn = [i for i, b in enumerate(battles) if not b.attack]
    out = [0] * len(battles)
    gains = _even_split(max(delta, 0), len(att)) if att else []
    losses = _even_split(min(delta, 0), len(dfn)) if dfn else []
    for i, v in zip(att, gains, strict=True):
        out[i] = v
    for i, v in zip(dfn, losses, strict=True):
        out[i] = v
    return out


def army_link(share_code: str | None) -> str | None:
    """In-game "copy army" deep link for a battle's army share code."""
    if not share_code:
        return None
    return f"https://link.clashofclans.com/en?action=CopyArmy&army={share_code}"


# ------------------------------------------------------------------ trophies per battle

# Legend I trophies by stars, as a share of destruction %: matches the in-game numbers exactly
# (e.g. 3★ = 40, 2★ 88% = 28, 1★ 56% = 11).
LEGEND_TROPHY_RATE = {1: 0.20, 2: 0.32, 3: 0.40}

# Tiers below Legend I (battle type "ranked"): the attacker earns the same share of destruction
# (0★: 0-4 trophies), and the defender gains what's left of 40 (0 against a triple). Matches the
# game's Legend II battle log. The API's trophy count doesn't follow these battles (it stays put
# all week), so this rule is the only source of their trophies. db/fixes.sql applies it to rows
# stored before it existed; keep the two in step.
RANKED_TROPHY_RATE = {0: 0.08, **LEGEND_TROPHY_RATE}
RANKED_BATTLE_TROPHIES = 40


def legend_trophies(b: Battle) -> int | None:
    """Signed trophies for a Legend I battle (+ attack, - defense), or None when unknown
    (other battle types, or 0-star battles)."""
    rate = LEGEND_TROPHY_RATE.get(b.stars) if b.battle_type == "legend" else None
    if rate is None:
        return None
    value = math.floor(b.destruction * rate + 0.5)
    return value if b.attack else -value


def ranked_trophies(b: Battle) -> int | None:
    """Trophies gained in a battle below Legend I (attack or defense; both gain), or None for
    other battle types."""
    rate = RANKED_TROPHY_RATE.get(b.stars) if b.battle_type == "ranked" else None
    if rate is None:
        return None
    gain = math.floor(b.destruction * rate + 0.5)
    return gain if b.attack else RANKED_BATTLE_TROPHIES - gain


def rule_trophies(b: Battle) -> int | None:
    """Trophies from the stars/destruction rule (Legend I or the tiers below), or None."""
    return legend_trophies(b) if b.battle_type == "legend" else ranked_trophies(b)


def battle_trophies(
    battles: Sequence[Battle], fresh_keys: set[str], delta: int, *, defenses_gain: bool = False
) -> list[int]:
    """Trophies for each battle (oldest first).

    Legend I and lower-tier ranked battles use the stars/destruction rule. For the rest, the
    observed trophy change ``delta`` (minus what the Legend I rule already explains) is split
    across the battles that are new since the last read; older backfilled battles with no rule
    get 0. Lower-tier ranked battles never explain ``delta``: the API's count doesn't follow them.
    """
    known = [rule_trophies(b) for b in battles]
    out = [k or 0 for k in known]
    open_idx = [
        i for i, (b, k) in enumerate(zip(battles, known, strict=True)) if k is None and b.key in fresh_keys
    ]
    if open_idx:
        explained = sum(
            k
            for b, k in zip(battles, known, strict=True)
            if k is not None and b.key in fresh_keys and b.battle_type == "legend"
        )
        split = assign_trophies(
            [battles[i] for i in open_idx], delta - explained, defenses_gain=defenses_gain
        )
        for i, v in zip(open_idx, split, strict=True):
            out[i] = v
    return out


def estimate_legend_result(trophies: int) -> tuple[int, int] | None:
    """(stars, destruction %) a Legend I battle most likely had, from its trophy change alone, for
    battles inferred from trophy changes (the battle log can lag behind for defenses). The amount
    fixes the stars except at the edges: 40 is a triple; 21-32 two stars (0.32 x 99% is the most a
    2-star can earn); 5-15 one star; 0-4, 16-20 and 33-39 are ambiguous or not from the rule (None).
    The % is the trophies' share of the band, kept under 100 when it isn't a triple."""
    t = abs(trophies)
    if t == 40:
        return 3, 100
    if 21 <= t <= 32:
        return 2, min(99, round(t / 0.32))
    if 5 <= t <= 15:
        return 1, min(99, round(t / 0.2))
    return None
