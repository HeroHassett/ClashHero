-- War target calling ("/caller"). One caller per enemy base position per war.
CREATE TABLE IF NOT EXISTS war_callers (
    war_id      INTEGER NOT NULL REFERENCES wars (id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,          -- enemy map position
    player_tag  TEXT,                      -- our attacker (if a Clash account was given)
    user_id     INTEGER,                   -- Discord user who claimed it
    note        TEXT,
    assigned_by INTEGER NOT NULL,
    assigned_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (war_id, position)
);
