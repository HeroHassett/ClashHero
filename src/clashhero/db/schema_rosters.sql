-- Rosters: sign-up lists for CWL, wars, esports or trophy pushes.

CREATE TABLE IF NOT EXISTS rosters (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id              INTEGER NOT NULL,
    clan_tag              TEXT    NOT NULL,
    name                  TEXT    NOT NULL,
    category              TEXT    NOT NULL DEFAULT 'cwl',   -- cwl | war | esports | trophy
    max_members           INTEGER NOT NULL DEFAULT 65,
    min_th                INTEGER,
    max_th                INTEGER,
    min_heroes            INTEGER,                          -- combined hero levels
    max_accounts_per_user INTEGER,
    allow_unlinked        INTEGER NOT NULL DEFAULT 0,
    allow_multi_signup    INTEGER NOT NULL DEFAULT 0,       -- allow being in >1 roster of this category
    allow_group_selection INTEGER NOT NULL DEFAULT 1,
    role_id               INTEGER,                          -- given on signup
    sort_by               TEXT    NOT NULL DEFAULT 'th_heroes',
    start_time            TEXT,
    end_time              TEXT,
    color                 INTEGER,
    closed                INTEGER NOT NULL DEFAULT 0,
    channel_id            INTEGER,                          -- where the roster is posted
    message_id            INTEGER,
    created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (guild_id, name COLLATE NOCASE)
);
CREATE INDEX IF NOT EXISTS idx_rosters_guild ON rosters (guild_id, category);

CREATE TABLE IF NOT EXISTS roster_groups (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    roster_id INTEGER NOT NULL REFERENCES rosters (id) ON DELETE CASCADE,
    name      TEXT    NOT NULL,
    position  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (roster_id, name COLLATE NOCASE)
);

CREATE TABLE IF NOT EXISTS roster_members (
    roster_id    INTEGER NOT NULL REFERENCES rosters (id) ON DELETE CASCADE,
    player_tag   TEXT    NOT NULL,
    user_id      INTEGER,
    group_id     INTEGER REFERENCES roster_groups (id) ON DELETE SET NULL,
    name         TEXT,
    town_hall    INTEGER,
    heroes_total INTEGER,
    signed_up_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (roster_id, player_tag)
);
CREATE INDEX IF NOT EXISTS idx_roster_members_tag ON roster_members (player_tag);
