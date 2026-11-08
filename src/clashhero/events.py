"""Typed events produced by the collector (services/collector.py).

Each event is dispatched on the bot as ``bot.dispatch(event.event_name, event)``, so a cog
listens with e.g.::

    @commands.Cog.listener()
    async def on_clash_member_join(self, event: MemberJoined): ...

Events describe what changed; consumers decide which guilds/channels care
(via ``db.clan_log_targets(clan_tag, log_type)``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from clashhero.services.snapshots import MemberSnap


@dataclass(frozen=True)
class ClashEvent:
    event_name: ClassVar[str] = "clash_event"


# ---------------------------------------------------------------- clan / membership


@dataclass(frozen=True)
class MemberJoined(ClashEvent):
    event_name: ClassVar[str] = "clash_member_join"
    clan_tag: str
    clan_name: str
    member: MemberSnap
    member_count: int


@dataclass(frozen=True)
class MemberLeft(ClashEvent):
    event_name: ClassVar[str] = "clash_member_leave"
    clan_tag: str
    clan_name: str
    member: MemberSnap
    member_count: int


@dataclass(frozen=True)
class RoleChanged(ClashEvent):
    event_name: ClassVar[str] = "clash_role_change"
    clan_tag: str
    clan_name: str
    player_tag: str
    name: str
    old_role: str
    new_role: str


@dataclass(frozen=True)
class NameChanged(ClashEvent):
    event_name: ClassVar[str] = "clash_name_change"
    clan_tag: str | None
    player_tag: str
    old_name: str
    new_name: str


@dataclass(frozen=True)
class DonationsChanged(ClashEvent):
    """Positive deltas since the previous poll."""

    event_name: ClassVar[str] = "clash_donations"
    clan_tag: str
    clan_name: str
    player_tag: str
    name: str
    donated: int
    received: int


@dataclass(frozen=True)
class TownHallUpgraded(ClashEvent):
    event_name: ClassVar[str] = "clash_town_hall_upgrade"
    clan_tag: str | None
    player_tag: str
    name: str
    old_level: int
    new_level: int


@dataclass(frozen=True)
class LeagueChanged(ClashEvent):
    event_name: ClassVar[str] = "clash_league_change"
    clan_tag: str | None
    player_tag: str
    name: str
    old_league: str | None
    new_league: str | None
    trophies: int


@dataclass(frozen=True)
class WarPreferenceChanged(ClashEvent):
    event_name: ClassVar[str] = "clash_war_preference"
    clan_tag: str | None
    player_tag: str
    name: str
    opted_in: bool


@dataclass(frozen=True)
class ClanAttributeChanged(ClashEvent):
    """attribute is one of: level, war_league, capital_league, capital_hall."""

    event_name: ClassVar[str] = "clash_clan_change"
    clan_tag: str
    clan_name: str
    attribute: str
    old: object
    new: object


# ---------------------------------------------------------------- progression


@dataclass(frozen=True)
class ClanGamesProgress(ClashEvent):
    event_name: ClassVar[str] = "clash_clan_games"
    clan_tag: str | None
    player_tag: str
    name: str
    points: int  # delta since last poll
    total: int  # points this Clan Games so far


@dataclass(frozen=True)
class CapitalContribution(ClashEvent):
    event_name: ClassVar[str] = "clash_capital_contribution"
    clan_tag: str | None
    player_tag: str
    name: str
    amount: int


@dataclass(frozen=True)
class LegendBattle(ClashEvent):
    """An inferred ranked attack or defense. ``trophies`` is signed."""

    event_name: ClassVar[str] = "clash_legend_battle"
    player_tag: str
    name: str
    clan_tag: str | None
    kind: str  # attack | defense
    trophies: int
    trophies_after: int
    occurred_at: datetime
    legend_day: str


# ---------------------------------------------------------------- war / capital


@dataclass(frozen=True)
class WarAttackMade(ClashEvent):
    """A new attack in a tracked clan's war (by either side). ``attack`` is a war_attacks row
    as a dict; ``war`` is a wars row as a dict."""

    event_name: ClassVar[str] = "clash_war_attack"
    clan_tag: str
    war: dict
    attack: dict
    attacker_name: str
    defender_name: str


@dataclass(frozen=True)
class WarStateChanged(ClashEvent):
    """old_state is None for a newly seen war. On ``warEnded`` consumers can query
    war_members/war_attacks for missed attacks."""

    event_name: ClassVar[str] = "clash_war_state"
    clan_tag: str
    war: dict
    old_state: str | None
    new_state: str


@dataclass(frozen=True)
class WarUpdated(ClashEvent):
    """Fired after every war poll (for live embeds)."""

    event_name: ClassVar[str] = "clash_war_update"
    clan_tag: str
    war: dict


@dataclass(frozen=True)
class CWLLineupChanged(ClashEvent):
    event_name: ClassVar[str] = "clash_cwl_lineup_change"
    clan_tag: str
    season: str
    added: list[str] = field(default_factory=list)  # player tags
    removed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RaidAttacksMade(ClashEvent):
    event_name: ClassVar[str] = "clash_raid_attack"
    clan_tag: str
    player_tag: str
    name: str
    attacks: int  # delta
    loot: int  # delta
    total_attacks: int
    attack_limit: int


@dataclass(frozen=True)
class RaidWeekendEnded(ClashEvent):
    event_name: ClassVar[str] = "clash_raid_end"
    clan_tag: str
    start_time: str


@dataclass(frozen=True)
class MaintenanceChanged(ClashEvent):
    event_name: ClassVar[str] = "clash_maintenance"
    started: bool
    at: datetime
