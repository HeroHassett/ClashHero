"""Columns added to the ranking tables after they first shipped (see Database._add_missing_columns)."""

COLUMNS = [
    # The player's ranked league in a country leaderboard ("Legend I", ...): finds Legend I players
    # for the daily full ranking.
    ("player_country_ranks", "league", "TEXT"),
]
