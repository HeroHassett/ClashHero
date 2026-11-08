-- Base layouts a server keeps, their posts, and when each was used in the current CWL.

CREATE TABLE IF NOT EXISTS war_bases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    name       TEXT    NOT NULL,
    town_hall  INTEGER NOT NULL,
    type       TEXT    NOT NULL DEFAULT 'war',   -- war | cwl | legend | farming | hybrid | fun
    link       TEXT,                             -- in-game copy link
    image_url  TEXT,
    notes      TEXT,
    added_by   INTEGER NOT NULL,
    channel_id INTEGER,                          -- last /bases post
    message_id INTEGER,
    added_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (guild_id, name COLLATE NOCASE)
);

CREATE TABLE IF NOT EXISTS base_usages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    base_id    INTEGER NOT NULL REFERENCES war_bases (id) ON DELETE CASCADE,
    season_id  TEXT    NOT NULL,                 -- CWL season "YYYY-MM"
    war_round  INTEGER,
    player_tag TEXT,
    used_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_base_usages_season ON base_usages (season_id, base_id);

CREATE TABLE IF NOT EXISTS base_settings (
    guild_id INTEGER PRIMARY KEY,
    voting   INTEGER NOT NULL DEFAULT 1
);
