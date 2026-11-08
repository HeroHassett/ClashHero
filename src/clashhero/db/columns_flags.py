"""Columns added to the flags table after it first shipped (see Database._add_missing_columns)."""

COLUMNS = [
    # Strike weight 1-10; bans keep the default of 1 (their weight isn't counted).
    ("flags", "weight", "INTEGER NOT NULL DEFAULT 1"),
]
