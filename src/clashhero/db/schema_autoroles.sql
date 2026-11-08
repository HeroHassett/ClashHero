-- Autorole mappings. kind: clan_role | town_hall | builder_hall | league | trophy_band |
-- builder_league | family | war. scope is a clan tag for clan_role/war, '' otherwise.
CREATE TABLE IF NOT EXISTS autoroles (
    guild_id INTEGER NOT NULL,
    kind     TEXT    NOT NULL,
    scope    TEXT    NOT NULL DEFAULT '',
    key      TEXT    NOT NULL,
    role_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, kind, scope, key)
);

CREATE TABLE IF NOT EXISTS autorole_settings (
    guild_id                 INTEGER PRIMARY KEY,
    auto_update              INTEGER NOT NULL DEFAULT 1,
    removal_delay_hours      INTEGER NOT NULL DEFAULT 0,
    allow_not_linked         INTEGER NOT NULL DEFAULT 0,  -- 1 = leave unlinked users' roles alone
    allow_non_family         INTEGER NOT NULL DEFAULT 0,  -- TH/league roles also for non-family accounts
    only_verified_clan_roles INTEGER NOT NULL DEFAULT 0,
    nick_enabled             INTEGER NOT NULL DEFAULT 0,
    family_format            TEXT,
    non_family_format        TEXT,
    nick_preference          TEXT    NOT NULL DEFAULT 'default'   -- default | best
);

CREATE TABLE IF NOT EXISTS autorole_pending_removals (
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    role_id      INTEGER NOT NULL,
    remove_after TEXT    NOT NULL,
    PRIMARY KEY (guild_id, user_id, role_id)
);
