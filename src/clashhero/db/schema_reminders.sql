-- Reminders (/reminders). type: war | cwl | raid | games.
CREATE TABLE IF NOT EXISTS reminders (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id                 INTEGER NOT NULL,
    type                     TEXT    NOT NULL,
    clans                    TEXT    NOT NULL,   -- space-separated clan tags
    duration_seconds         INTEGER NOT NULL,   -- fire this long before the event ends
    message                  TEXT    NOT NULL,
    channel_id               INTEGER NOT NULL,
    exclude_participant_list INTEGER NOT NULL DEFAULT 0,
    created_by               INTEGER,
    created_at               TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reminders_guild ON reminders (guild_id, type);

-- One row per reminder per event instance so each reminder fires once.
CREATE TABLE IF NOT EXISTS reminder_sent (
    reminder_id INTEGER NOT NULL REFERENCES reminders (id) ON DELETE CASCADE,
    event_key   TEXT    NOT NULL,
    sent_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (reminder_id, event_key)
);

-- Optional role filter per reminder type. mode: optin (only role holders are pinged)
-- or optout (role holders are not pinged).
CREATE TABLE IF NOT EXISTS reminder_config (
    guild_id INTEGER NOT NULL,
    type     TEXT    NOT NULL,
    role_id  INTEGER NOT NULL,
    mode     TEXT    NOT NULL,
    PRIMARY KEY (guild_id, type)
);
