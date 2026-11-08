-- Scheduled boards (/autoboard): a leaderboard/summary posted to a channel on a schedule, or one
-- "live" message edited in place every interval_min minutes.
CREATE TABLE IF NOT EXISTS autoboards (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    board_type   TEXT    NOT NULL,              -- see repos/autoboards.BOARD_TYPES
    clans        TEXT    NOT NULL DEFAULT '',   -- space-separated clan tags; '' = every server clan
    channel_id   INTEGER NOT NULL,
    schedule     TEXT    NOT NULL,              -- daily | weekly | season_end | legend_reset | live
    hour         INTEGER,                       -- daily / weekly (UTC)
    minute       INTEGER,
    weekday      INTEGER,                       -- weekly: 0 = Monday
    interval_min INTEGER,                       -- live: minutes between edits
    message_id   INTEGER,                       -- live: the message edited in place
    last_run     TEXT,                          -- slot last posted (fixed) / last edit (live)
    created_by   INTEGER NOT NULL,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_autoboards_guild ON autoboards (guild_id);
