"""Shared identifiers. Feature cogs use these keys so config and routing stay consistent."""

from __future__ import annotations

# Per-clan log feeds: key -> human label. Configured with /setup clan-logs.
CLAN_LOG_TYPES: dict[str, str] = {
    # clan
    "clan_achievements": "Clan achievements (level, war/capital league changes)",
    "donations": "Donation log",
    "legend_daily": "Legend attacks daily summary",
    # players
    "join_leave": "Member join / leave",
    "role_change": "Role change",
    "name_change": "Name change",
    "town_hall": "Town Hall upgrade",
    "war_preference": "War preference change",
    "league_change": "League change",
    "legend_battles": "Legend battle feed (every ranked attack/defense)",
    # war
    "war_attacks": "War attack feed",
    "cwl_attacks": "CWL attack feed",
    "war_missed": "War missed attacks",
    "cwl_missed": "CWL missed attacks",
    "war_state": "War start / end announcements",
    "cwl_lineup_change": "CWL lineup changes",
    # capital
    "capital_contributions": "Capital gold contributions",
    "capital_raids": "Capital raid attacks",
    "capital_summary": "Weekly raid summary",
    # clan games
    "clan_games": "Clan Games progress",
}

# Server-wide logs. Configured with /setup server-logs.
SERVER_LOG_TYPES: dict[str, str] = {
    "flag_alert": "Flagged player joined a family clan",
    "roster_change": "Roster changes",
    "maintenance": "API maintenance start / end",
    "welcome": "Account linked (welcome)",
}

# Live (auto-edited) message kinds stored in live_messages.kind.
LIVE_KINDS: dict[str, str] = {
    "clan_embed": "Clan showcase",
    "war_embed": "Current war",
    "cwl_embed": "CWL round",
    "clan_games": "Clan Games leaderboard",
    "last_seen": "Last seen / activity",
    "legend_board": "Legend leaderboard",
}

ROLE_NAMES = {"leader": "Leader", "coLeader": "Co-Leader", "admin": "Elder", "member": "Member"}
ROLE_RANK = {"leader": 4, "coLeader": 3, "admin": 2, "member": 1}

DEFAULT_EMBED_COLOR = 0x5865F2

# Required by Supercell's Fan Content Policy (https://supercell.com/en/fan-content-policy/).
SUPERCELL_DISCLAIMER = (
    "This material is unofficial and is not endorsed by Supercell. For more information see "
    "Supercell's Fan Content Policy: www.supercell.com/fan-content-policy."
)
