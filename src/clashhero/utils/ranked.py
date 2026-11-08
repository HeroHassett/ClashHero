"""Ranked league tiers (the 2025+ ranked system).

The API reports a player's ranked league as ``leagueTier`` (e.g. "Legend I", "Electro League 32").
The legacy ``league`` field now reads "Unranked" for everyone, and coc.py 4.0 doesn't expose
``leagueTier`` yet, so we read it from the raw API payload (the client runs with
``raw_attribute=True``).

Tournament rules (adjust here if Supercell changes them):
- Legend I runs 4-week tournaments (the 28-day game season); 8 attacks + 8 defenses per day.
- Every other tier runs weekly tournaments (Monday 05:00 UTC to Monday 05:00 UTC).
- Legend II allows 30 attacks per week and Legend III 24 ("battles" in Supercell's wording).
- Per-battle tracking covers Dragon League 28 and up.
"""

from __future__ import annotations

from typing import Any

# Official order from GET /leaguetiers (index == tier number).
LEAGUE_TIERS: tuple[str, ...] = (
    "Unranked",
    "Skeleton League 1", "Skeleton League 2", "Skeleton League 3",
    "Barbarian League 4", "Barbarian League 5", "Barbarian League 6",
    "Archer League 7", "Archer League 8", "Archer League 9",
    "Wizard League 10", "Wizard League 11", "Wizard League 12",
    "Valkyrie League 13", "Valkyrie League 14", "Valkyrie League 15",
    "Witch League 16", "Witch League 17", "Witch League 18",
    "Golem League 19", "Golem League 20", "Golem League 21",
    "P.E.K.K.A League 22", "P.E.K.K.A League 23", "P.E.K.K.A League 24",
    "Titan League 25", "Titan League 26", "Titan League 27",
    "Dragon League 28", "Dragon League 29", "Dragon League 30",
    "Electro League 31", "Electro League 32", "Electro League 33",
    "Legend III", "Legend II", "Legend I",
)  # fmt: skip
_TIER_INDEX = {name.lower(): i for i, name in enumerate(LEAGUE_TIERS)}
_TIER_INDEX["legend league"] = len(LEAGUE_TIERS) - 1  # legacy name

LEGEND_ONE = "Legend I"
MIN_TRACKED_TIER = _TIER_INDEX["dragon league 28"]
DAILY_ATTACK_CAP = {"legend i": 8}
WEEKLY_ATTACK_CAP = {"legend ii": 30, "legend iii": 24}


def tier_number(name: str | None) -> int | None:
    return _TIER_INDEX.get(name.lower()) if name else None


def is_tracked_ranked(name: str | None) -> bool:
    """Tiers where per-battle tracking is meaningful (Dragon League 28 and up)."""
    n = tier_number(name)
    return n is not None and n >= MIN_TRACKED_TIER


def is_legend_tier(name: str | None) -> bool:
    n = tier_number(name)
    return n is not None and n >= _TIER_INDEX["legend iii"]


def is_legend_one(name: str | None) -> bool:
    return tier_number(name) == _TIER_INDEX["legend i"]


def daily_attack_cap(name: str | None) -> int | None:
    """Attacks (and defenses) allowed per legend day, or None when the tier has no daily cap."""
    return DAILY_ATTACK_CAP.get(name.lower()) if name else None


def weekly_attack_cap(name: str | None) -> int | None:
    return WEEKLY_ATTACK_CAP.get(name.lower()) if name else None


def league_icon(obj: Any) -> str | None:
    raw = getattr(obj, "_raw_data", None)
    if isinstance(raw, dict) and isinstance(raw.get("leagueTier"), dict):
        return (raw["leagueTier"].get("iconUrls") or {}).get("small")
    league = getattr(obj, "league", None)
    return getattr(getattr(league, "icon", None), "url", None)


def league_name(obj: Any) -> str | None:
    """Ranked league name for a coc.py Player/ClanMember/RankedPlayer.

    Prefers ``leagueTier`` from the raw payload; falls back to the legacy ``league`` field.
    """
    raw = getattr(obj, "_raw_data", None)
    if isinstance(raw, dict):
        tier = raw.get("leagueTier")
        if isinstance(tier, dict) and tier.get("name"):
            return tier["name"]
    league = getattr(obj, "league", None)
    return getattr(league, "name", None) if league is not None else None
