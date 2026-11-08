"""Per-reminder target filters (see repos/reminders.Filters). NULL means "no filter"."""

COLUMNS = [
    ("reminders", "min_th", "INTEGER"),
    ("reminders", "max_th", "INTEGER"),
    ("reminders", "clan_roles", "TEXT"),  # space-separated API roles: member admin coLeader leader
    ("reminders", "war_type", "TEXT"),  # war reminders only: random | friendly | cwl
    ("reminders", "games_below", "INTEGER"),  # Clan Games: only ping members below N points
    ("reminders", "raid_min_left", "INTEGER"),  # raids: only ping members with >= N attacks left
]
