"""Plain snapshots of API objects plus pure diff functions.

Keeping these independent of coc.py objects and Discord makes them trivial to unit test and
lets us persist the last-seen state as JSON between restarts.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from clashhero.utils.ranked import is_legend_one, is_tracked_ranked, league_name

try:  # unit categories from coc.py's static game data
    from coc import PETS_ORDER, SIEGE_MACHINE_ORDER, SUPER_TROOP_ORDER
except ImportError:  # pragma: no cover
    PETS_ORDER, SIEGE_MACHINE_ORDER, SUPER_TROOP_ORDER = [], [], []

GAMES_CHAMPION = "Games Champion"
MAX_TROPHIES_PER_BATTLE = 40
# A single poll gap can't plausibly explain more than this; larger jumps are resets.
MAX_PLAUSIBLE_DELTA = 8 * MAX_TROPHIES_PER_BATTLE


def _name(obj: Any) -> str | None:
    return getattr(obj, "name", None) if obj is not None else None


def _role(obj: Any) -> str | None:
    role = getattr(obj, "role", None)
    return getattr(role, "value", role) if role is not None else None


@dataclass(frozen=True)
class MemberSnap:
    tag: str
    name: str
    role: str | None
    town_hall: int
    trophies: int
    league: str | None
    donations: int
    received: int
    exp_level: int = 0
    builder_trophies: int = 0

    @classmethod
    def from_coc(cls, m: Any) -> MemberSnap:
        return cls(
            tag=m.tag,
            name=m.name,
            role=_role(m),
            town_hall=getattr(m, "town_hall", 0) or 0,
            trophies=m.trophies or 0,
            league=league_name(m),
            donations=m.donations or 0,
            received=m.received or 0,
            exp_level=getattr(m, "exp_level", 0) or 0,
            builder_trophies=getattr(m, "builder_base_trophies", 0) or 0,
        )


@dataclass(frozen=True)
class ClanSnap:
    tag: str
    name: str
    level: int
    war_league: str | None
    capital_league: str | None
    capital_hall: int | None
    members: dict[str, MemberSnap] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)  # see clan_settings()

    @classmethod
    def from_coc(cls, c: Any) -> ClanSnap:
        capital_hall = None
        for d in getattr(c, "capital_districts", None) or []:
            if getattr(d, "id", None) == 70000000:  # Capital Peak
                capital_hall = getattr(d, "hall_level", None)
        return cls(
            tag=c.tag,
            name=c.name,
            level=c.level,
            war_league=_name(getattr(c, "war_league", None)),
            capital_league=_name(getattr(c, "capital_league", None)),
            capital_hall=capital_hall,
            members={m.tag: MemberSnap.from_coc(m) for m in c.members},
            settings=clan_settings(getattr(c, "_raw_data", None)),
        )

    def to_json(self) -> dict:
        d = asdict(self)
        d["members"] = {k: asdict(v) for k, v in self.members.items()}
        return d

    @classmethod
    def from_json(cls, d: dict) -> ClanSnap:
        members = {k: MemberSnap(**v) for k, v in d.get("members", {}).items()}
        return cls(**{**d, "members": members})


@dataclass(frozen=True)
class PlayerSnap:
    tag: str
    name: str
    town_hall: int
    exp_level: int
    trophies: int
    best_trophies: int
    league: str | None
    attack_wins: int
    defense_wins: int
    war_stars: int
    war_opted_in: bool | None
    clan_tag: str | None
    clan_role: str | None
    games_points: int  # lifetime "Games Champion" achievement value
    capital_contributions: int
    heroes_total: int
    donations: int
    received: int
    builder_hall: int = 0
    builder_trophies: int = 0
    builder_league: str | None = None
    clan_name: str | None = None
    units: dict[str, int] = field(default_factory=dict)  # "Kind:Name" -> level, see unit_levels()
    super_troops: tuple[str, ...] = ()  # currently boosted

    @classmethod
    def from_coc(cls, p: Any) -> PlayerSnap:
        raw = getattr(p, "_raw_data", None)
        if isinstance(raw, dict):  # raw payload: unaffected by units coc.py doesn't know yet
            games = next(
                (a.get("value", 0) for a in raw.get("achievements") or [] if a.get("name") == GAMES_CHAMPION),
                0,
            )
            heroes = sum(
                h.get("level", 0) for h in raw.get("heroes") or [] if h.get("village", "home") == "home"
            )
        else:
            games = next(
                (a.value for a in getattr(p, "achievements", None) or [] if a.name == GAMES_CHAMPION), 0
            )
            heroes = sum(
                h.level for h in getattr(p, "heroes", None) or [] if getattr(h, "is_home_base", True)
            )
        clan = getattr(p, "clan", None)
        return cls(
            tag=p.tag,
            name=p.name,
            town_hall=p.town_hall or 0,
            exp_level=p.exp_level or 0,
            trophies=p.trophies or 0,
            best_trophies=p.best_trophies or 0,
            league=league_name(p),
            attack_wins=p.attack_wins or 0,
            defense_wins=p.defense_wins or 0,
            war_stars=p.war_stars or 0,
            war_opted_in=getattr(p, "war_opted_in", None),
            clan_tag=getattr(clan, "tag", None),
            clan_role=_role(p),
            games_points=games,
            capital_contributions=getattr(p, "clan_capital_contributions", 0) or 0,
            heroes_total=heroes,
            donations=p.donations or 0,
            received=p.received or 0,
            builder_hall=getattr(p, "builder_hall", 0) or 0,
            builder_trophies=getattr(p, "builder_base_trophies", 0) or 0,
            builder_league=_name(getattr(p, "builder_base_league", None)),
            clan_name=getattr(clan, "name", None),
            units=unit_levels(getattr(p, "_raw_data", None)),
            super_troops=active_super_troops(getattr(p, "_raw_data", None)),
        )

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> PlayerSnap:
        known = set(cls.__dataclass_fields__)
        d = {k: v for k, v in d.items() if k in known}
        if "super_troops" in d:
            d["super_troops"] = tuple(d["super_troops"])
        return cls(**d)


# ------------------------------------------------------------------ clan diff


@dataclass(frozen=True)
class ClanDiff:
    joined: list[MemberSnap] = field(default_factory=list)
    left: list[MemberSnap] = field(default_factory=list)
    role_changes: list[tuple[MemberSnap, MemberSnap]] = field(default_factory=list)
    name_changes: list[tuple[MemberSnap, MemberSnap]] = field(default_factory=list)
    # (member, donated_delta, received_delta)
    donations: list[tuple[MemberSnap, int, int]] = field(default_factory=list)
    # (attribute, old, new)
    clan_changes: list[tuple[str, Any, Any]] = field(default_factory=list)


def counter_delta(old: int, new: int) -> int:
    """Delta for a counter that resets (season end, leave/rejoin): a drop means it restarted."""
    return new - old if new >= old else new


def diff_clan(old: ClanSnap, new: ClanSnap) -> ClanDiff:
    d = ClanDiff()
    for tag, m in new.members.items():
        prev = old.members.get(tag)
        if prev is None:
            d.joined.append(m)
            continue
        if prev.role != m.role:
            d.role_changes.append((prev, m))
        if prev.name != m.name:
            d.name_changes.append((prev, m))
        don = counter_delta(prev.donations, m.donations)
        rec = counter_delta(prev.received, m.received)
        if don or rec:
            d.donations.append((m, don, rec))
    d.left.extend(m for tag, m in old.members.items() if tag not in new.members)
    for attr in ("level", "war_league", "capital_league", "capital_hall"):
        a, b = getattr(old, attr), getattr(new, attr)
        if a != b and a is not None and b is not None:
            d.clan_changes.append((attr, a, b))
    return d


# ------------------------------------------------------------------ player diff


@dataclass(frozen=True)
class InferredBattle:
    kind: str  # attack | defense
    trophies: int  # signed


def is_ranked_tracked_league(league: str | None) -> bool:
    """Leagues where per-battle trophy inference is meaningful (Dragon League 28 and up)."""
    return is_tracked_ranked(league)


def infer_battles(old: PlayerSnap, new: PlayerSnap) -> list[InferredBattle]:
    """Infer ranked attacks/defenses from trophy + win-counter deltas between two polls.

    The API has no battle log, so this is a heuristic (same approach as other legend
    trackers): trophy gains are attacks, losses are defenses. Multiple battles between polls
    are split using the attack/defense win counters when they moved by more than one.
    Implausibly large jumps (tournament resets, promotions) produce nothing.
    """
    if not (is_ranked_tracked_league(old.league) and is_ranked_tracked_league(new.league)):
        return []
    delta = new.trophies - old.trophies
    if abs(delta) > MAX_PLAUSIBLE_DELTA:
        return []
    att = max(0, new.attack_wins - old.attack_wins)
    dfn = max(0, new.defense_wins - old.defense_wins)
    if delta > 0:
        return _split("attack", delta, max(att, 1))  # below Legend I this may be a defense: unknowable
    if delta < 0 and not is_legend_one(new.league):
        return []  # outside Legend I trophies only drop at resets, never in battle
    if delta < 0:
        return _split("defense", delta, max(1, math.ceil(-delta / MAX_TROPHIES_PER_BATTLE)))
    if att:  # trophies unchanged but an attack registered (e.g. 0-trophy hit)
        return [InferredBattle("attack", 0) for _ in range(att)]
    if dfn:  # successful defense for 0 trophies
        return [InferredBattle("defense", 0) for _ in range(dfn)]
    return []


def _split(kind: str, total: int, n: int) -> list[InferredBattle]:
    n = max(1, min(n, abs(total)) if total else 1)
    base, rem = divmod(abs(total), n)
    sign = 1 if total > 0 else -1
    return [InferredBattle(kind, sign * (base + (1 if i < rem else 0))) for i in range(n)]


def has_activity(old: PlayerSnap, new: PlayerSnap) -> bool:
    """Any change that implies the player was online since the last poll."""
    fields = (
        "name",
        "exp_level",
        "attack_wins",
        "war_stars",
        "games_points",
        "capital_contributions",
        "donations",
        "heroes_total",
        "town_hall",
        "builder_hall",
        "builder_trophies",
        "war_opted_in",
    )
    if any(getattr(old, f) != getattr(new, f) for f in fields):
        return True
    # Trophy gains need the player online; losses can be defenses while offline.
    return new.trophies > old.trophies


# ------------------------------------------------------------------ units and clan settings


def unit_levels(raw: dict | None) -> dict[str, int]:
    """Home-village unit levels from a raw player payload: {"Kind:Name": level}.

    Kinds: Troop, Siege, Pet, Spell, Hero, Equipment. Super troops are skipped (their level mirrors
    the base troop); boosts are tracked separately by active_super_troops().
    """
    if not isinstance(raw, dict):
        return {}
    supers, pets, sieges = set(SUPER_TROOP_ORDER), set(PETS_ORDER), set(SIEGE_MACHINE_ORDER)
    out: dict[str, int] = {}
    for key, default_kind in (
        ("troops", "Troop"),
        ("spells", "Spell"),
        ("heroes", "Hero"),
        ("heroEquipment", "Equipment"),
    ):
        for u in raw.get(key) or []:
            name = u.get("name")
            if not name or u.get("village", "home") != "home" or name in supers:
                continue
            kind = "Pet" if name in pets else "Siege" if name in sieges else default_kind
            out[f"{kind}:{name}"] = int(u.get("level") or 0)
    return out


def active_super_troops(raw: dict | None) -> tuple[str, ...]:
    if not isinstance(raw, dict):
        return ()
    return tuple(sorted(u["name"] for u in raw.get("troops") or [] if u.get("superTroopIsActive")))


def diff_units(old: dict[str, int], new: dict[str, int]) -> list[tuple[str, str, int | None, int]]:
    """(kind, name, old level or None if newly unlocked, new level) for every level increase.

    Returns nothing when ``old`` is empty (no baseline yet).
    """
    if not old:
        return []
    out = []
    for key, level in sorted(new.items()):
        before = old.get(key)
        if before is None or level > before:
            kind, _, name = key.partition(":")
            out.append((kind, name, before, level))
    return out


_SETTING_KEYS = {
    "description": "description",
    "type": "type",
    "requiredTownhallLevel": "required_town_hall",
    "requiredTrophies": "required_trophies",
    "requiredBuilderBaseTrophies": "required_builder_trophies",
    "warFrequency": "war_frequency",
    "isWarLogPublic": "public_war_log",
    "isFamilyFriendly": "family_friendly",
}


def clan_settings(raw: dict | None) -> dict[str, Any]:
    """Clan settings a leader can change, from a raw clan payload (JSON-friendly values)."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {name: raw.get(key) for key, name in _SETTING_KEYS.items() if key in raw}
    out["location"] = (raw.get("location") or {}).get("name")
    out["chat_language"] = (raw.get("chatLanguage") or {}).get("name")
    out["labels"] = sorted(lbl.get("name", "") for lbl in raw.get("labels") or [])
    return out


def diff_settings(old: dict[str, Any], new: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """(setting, old, new) for settings present in both snapshots whose value changed."""
    if not old or not new:
        return []
    return [(k, old[k], new[k]) for k in sorted(new) if k in old and old[k] != new[k]]
