"""Columns added to core tables after they first shipped (see Database._add_missing_columns)."""

COLUMNS = [
    # Ranked battles from the player battle log (NULL for battles inferred from trophy changes).
    ("legend_events", "battle_type", "TEXT"),
    ("legend_events", "opponent_tag", "TEXT"),
    ("legend_events", "opponent_name", "TEXT"),
    ("legend_events", "opponent_th", "INTEGER"),
    ("legend_events", "stars", "INTEGER"),
    ("legend_events", "destruction", "INTEGER"),
    ("legend_events", "army_share_code", "TEXT"),
    ("legend_events", "battle_time", "TEXT"),
    # Both clans' names and badges, for the war logs.
    ("wars", "clan_name", "TEXT"),
    ("wars", "clan_badge_url", "TEXT"),
    ("wars", "opponent_badge_url", "TEXT"),
]
