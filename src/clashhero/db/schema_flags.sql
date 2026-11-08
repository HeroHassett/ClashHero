-- Player flags (bans / strikes), per server.
CREATE TABLE IF NOT EXISTS flags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    player_tag  TEXT    NOT NULL,
    flag_type   TEXT    NOT NULL,          -- ban | strike
    reason      TEXT,
    created_by  INTEGER NOT NULL,
    created_at  TEXT    NOT NULL,
    expires_at  TEXT                       -- NULL = never
);
CREATE INDEX IF NOT EXISTS idx_flags_guild_player ON flags (guild_id, player_tag);
