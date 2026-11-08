"""Player battle log (``GET /players/{tag}/battlelog``): parsing and bookkeeping.

The endpoint returns a player's most recent battles (about 40), newest first, with opponent,
stars, destruction, loot and the attacking army's share code. coc.py 4.0 doesn't wrap it, so the
collector calls it directly.

``battleType`` values seen: ``legend`` (Legend I), ``ranked`` (other ranked tiers) and
``homeVillage`` (regular multiplayer). It does not include the trophy change per battle, so the
collector splits the trophy delta it observes between polls across the new ranked battles.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

RANKED_TYPES = frozenset({"legend", "ranked"})


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

    def to_json(self) -> dict:
        return {"timestamp": self.timestamp, "keys": self.keys, "pending_delta": self.pending_delta}

    @classmethod
    def from_json(cls, d: dict | None) -> Cursor:
        d = d or {}
        return cls(d.get("timestamp"), list(d.get("keys") or []), int(d.get("pending_delta") or 0))


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
    return fresh, Cursor(newest, keys_at_newest, cursor.pending_delta)


def _even_split(total: int, n: int) -> list[int]:
    if n <= 0:
        return []
    sign = 1 if total >= 0 else -1
    base, rem = divmod(abs(total), n)
    return [sign * (base + (1 if i < rem else 0)) for i in range(n)]


def assign_trophies(battles: Sequence[Battle], delta: int) -> list[int]:
    """Split an observed trophy change across ``battles`` (ranked, oldest first).

    Attacks only gain and defenses only lose, so gains go to attacks and losses to defenses.
    When both happen between two polls only the net change is known, so the split is approximate.
    """
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
