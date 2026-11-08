-- ClashHero core schema. Applied on every startup, so statements must be idempotent.
-- Feature modules may add their own tables in db/schema_<feature>.sql (loaded after this).
-- To add a column to a table that already shipped, declare it in db/columns_<feature>.py
-- (COLUMNS = [(table, column, declaration)]) rather than editing its CREATE TABLE.
-- Timestamps are ISO-8601 UTC strings.
-- Privacy conventions (services/privacy.py relies on them): a column named guild_id scopes a row
-- to a server; discord_user_id / user_id identify the Discord user the row is about;
-- created_by / added_by / assigned_by record who configured something.

------------------------------------------------------------------ server configuration

CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id             INTEGER PRIMARY KEY,
    bot_manager_role     INTEGER,      -- full admin over bot features
    roster_manager_role  INTEGER,
    links_manager_role   INTEGER,
    flags_manager_role   INTEGER,
    embed_color          INTEGER,      -- 0xRRGGBB
    timezone_offset_min  INTEGER NOT NULL DEFAULT 0
);

-- Clans a server manages ("family" clans).
CREATE TABLE IF NOT EXISTS guild_clans (
    guild_id   INTEGER NOT NULL,
    clan_tag   TEXT    NOT NULL,
    alias      TEXT,                    -- short name usable instead of the tag
    category   TEXT,                    -- e.g. "Main", "Feeder"
    sort_order INTEGER NOT NULL DEFAULT 0,
    added_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (guild_id, clan_tag)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_guild_clans_alias
    ON guild_clans (guild_id, alias COLLATE NOCASE) WHERE alias IS NOT NULL;

-- Default clan for a channel, so commands there need no clan argument.
CREATE TABLE IF NOT EXISTS channel_clans (
    channel_id INTEGER PRIMARY KEY,
    guild_id   INTEGER NOT NULL,
    clan_tag   TEXT    NOT NULL
);

-- Per-clan log feeds: one row per (guild, clan, log type). See constants.CLAN_LOG_TYPES.
CREATE TABLE IF NOT EXISTS clan_logs (
    guild_id     INTEGER NOT NULL,
    clan_tag     TEXT    NOT NULL,
    log_type     TEXT    NOT NULL,
    channel_id   INTEGER NOT NULL,
    ping_role_id INTEGER,
    color        INTEGER,
    PRIMARY KEY (guild_id, clan_tag, log_type)
);

-- Server-wide logs. See constants.SERVER_LOG_TYPES.
CREATE TABLE IF NOT EXISTS server_logs (
    guild_id   INTEGER NOT NULL,
    log_type   TEXT    NOT NULL,
    channel_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, log_type)
);

-- Messages the bot keeps editing in place (war embed, leaderboards, ...).
CREATE TABLE IF NOT EXISTS live_messages (
    guild_id   INTEGER NOT NULL,
    kind       TEXT    NOT NULL,        -- see constants.LIVE_KINDS
    scope      TEXT    NOT NULL DEFAULT '',  -- usually a clan tag; '' for server-wide
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    options    TEXT,                    -- JSON blob for kind-specific settings
    PRIMARY KEY (guild_id, kind, scope)
);

------------------------------------------------------------------ accounts

CREATE TABLE IF NOT EXISTS player_links (
    player_tag      TEXT    PRIMARY KEY,
    discord_user_id INTEGER NOT NULL,
    is_default      INTEGER NOT NULL DEFAULT 0,
    verified        INTEGER NOT NULL DEFAULT 0,
    linked_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_player_links_user ON player_links (discord_user_id);

------------------------------------------------------------------ collected game data
-- Written only by services/collector.py. Feature cogs read these.

CREATE TABLE IF NOT EXISTS clans (
    clan_tag       TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    level          INTEGER,
    member_count   INTEGER,
    war_league     TEXT,
    capital_league TEXT,
    badge_url      TEXT,
    updated_at     TEXT NOT NULL
);

-- Latest known state of every player we've seen (members of tracked clans + tracked players).
CREATE TABLE IF NOT EXISTS players (
    player_tag      TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    town_hall       INTEGER,
    exp_level       INTEGER,
    trophies        INTEGER,
    best_trophies   INTEGER,
    league          TEXT,
    war_stars       INTEGER,
    attack_wins     INTEGER,
    defense_wins    INTEGER,
    war_opted_in    INTEGER,
    clan_tag        TEXT,
    clan_role       TEXT,               -- member | admin | coLeader | leader
    heroes_total    INTEGER,            -- sum of home-village hero levels
    builder_hall    INTEGER,
    builder_trophies INTEGER,
    builder_league  TEXT,
    last_seen       TEXT,               -- estimated from observed activity
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_players_clan ON players (clan_tag);

CREATE TABLE IF NOT EXISTS player_names (
    player_tag TEXT NOT NULL,
    name       TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (player_tag, name)
);

-- Join/leave history for tracked clans.
CREATE TABLE IF NOT EXISTS clan_membership_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    clan_tag   TEXT NOT NULL,
    player_tag TEXT NOT NULL,
    kind       TEXT NOT NULL,           -- join | leave
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_membership_player ON clan_membership_events (player_tag);
CREATE INDEX IF NOT EXISTS idx_membership_clan ON clan_membership_events (clan_tag, occurred_at);

-- Per-season accumulated stats. season_id = utils.seasons.game_season().id.
-- Donations persist across leave/rejoin because we accumulate deltas.
CREATE TABLE IF NOT EXISTS season_stats (
    season_id        TEXT    NOT NULL,
    player_tag       TEXT    NOT NULL,
    clan_tag         TEXT    NOT NULL DEFAULT '',
    donations        INTEGER NOT NULL DEFAULT 0,
    received         INTEGER NOT NULL DEFAULT 0,
    capital_gold     INTEGER NOT NULL DEFAULT 0,
    attack_wins      INTEGER NOT NULL DEFAULT 0,
    activity         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (season_id, player_tag, clan_tag)
);

-- Clan Games points per calendar month (from the "Games Champion" achievement delta).
CREATE TABLE IF NOT EXISTS clan_games_points (
    games_id   TEXT    NOT NULL,        -- "YYYY-MM"
    player_tag TEXT    NOT NULL,
    clan_tag   TEXT    NOT NULL DEFAULT '',
    points     INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT    NOT NULL,
    PRIMARY KEY (games_id, player_tag)
);

-- Activity counts per player per hour bucket, for last-seen / activity graphs.
CREATE TABLE IF NOT EXISTS activity (
    player_tag TEXT    NOT NULL,
    hour       TEXT    NOT NULL,        -- "YYYY-MM-DDTHH"
    clan_tag   TEXT    NOT NULL DEFAULT '',
    count      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (player_tag, hour)
);

-- Inferred ranked/legend battles (from trophy and win-counter deltas between polls).
CREATE TABLE IF NOT EXISTS legend_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    player_tag    TEXT    NOT NULL,
    legend_day    TEXT    NOT NULL,     -- utils.seasons.legend_day_id()
    season_id     TEXT    NOT NULL,
    kind          TEXT    NOT NULL,     -- attack | defense
    trophies      INTEGER NOT NULL,     -- signed delta (+ for attacks, - for defenses)
    trophies_after INTEGER NOT NULL,
    occurred_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_legend_events_player ON legend_events (player_tag, legend_day);
CREATE INDEX IF NOT EXISTS idx_legend_events_day ON legend_events (legend_day);

-- Trophy count at the start of each legend day (taken at the first poll after reset).
CREATE TABLE IF NOT EXISTS legend_days (
    player_tag     TEXT    NOT NULL,
    legend_day     TEXT    NOT NULL,
    season_id      TEXT    NOT NULL,
    start_trophies INTEGER NOT NULL,
    end_trophies   INTEGER,
    PRIMARY KEY (player_tag, legend_day)
);

-- Wars (regular, friendly and CWL rounds) seen for tracked clans.
CREATE TABLE IF NOT EXISTS wars (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    clan_tag           TEXT    NOT NULL,
    opponent_tag       TEXT,
    opponent_name      TEXT,
    war_type           TEXT    NOT NULL,   -- random | friendly | cwl
    war_tag            TEXT,               -- CWL war tag
    cwl_season         TEXT,               -- "YYYY-MM" for CWL wars
    cwl_round          INTEGER,
    state              TEXT    NOT NULL,   -- preparation | inWar | warEnded
    team_size          INTEGER,
    attacks_per_member INTEGER,
    preparation_start  TEXT    NOT NULL,
    start_time         TEXT,
    end_time           TEXT,
    clan_stars         INTEGER DEFAULT 0,
    clan_destruction   REAL    DEFAULT 0,
    clan_attacks       INTEGER DEFAULT 0,
    opponent_stars     INTEGER DEFAULT 0,
    opponent_destruction REAL  DEFAULT 0,
    opponent_attacks   INTEGER DEFAULT 0,
    result             TEXT,               -- win | lose | tie (when ended)
    UNIQUE (clan_tag, preparation_start)
);
CREATE INDEX IF NOT EXISTS idx_wars_clan ON wars (clan_tag, preparation_start);

CREATE TABLE IF NOT EXISTS war_members (
    war_id       INTEGER NOT NULL REFERENCES wars (id) ON DELETE CASCADE,
    player_tag   TEXT    NOT NULL,
    name         TEXT    NOT NULL,
    town_hall    INTEGER,
    map_position INTEGER,
    is_opponent  INTEGER NOT NULL,
    PRIMARY KEY (war_id, player_tag)
);

CREATE TABLE IF NOT EXISTS war_attacks (
    war_id        INTEGER NOT NULL REFERENCES wars (id) ON DELETE CASCADE,
    attack_order  INTEGER NOT NULL,
    attacker_tag  TEXT    NOT NULL,
    defender_tag  TEXT    NOT NULL,
    stars         INTEGER NOT NULL,
    destruction   REAL    NOT NULL,
    duration      INTEGER,
    is_fresh      INTEGER NOT NULL,
    new_stars     INTEGER NOT NULL DEFAULT 0,  -- stars gained over the previous best on that base
    attacker_th   INTEGER,
    defender_th   INTEGER,
    attacker_pos  INTEGER,
    defender_pos  INTEGER,
    is_opponent   INTEGER NOT NULL,            -- 1 if made by the opposing clan
    seen_at       TEXT    NOT NULL,
    PRIMARY KEY (war_id, attack_order)
);
CREATE INDEX IF NOT EXISTS idx_war_attacks_attacker ON war_attacks (attacker_tag);
CREATE INDEX IF NOT EXISTS idx_war_attacks_defender ON war_attacks (defender_tag);

-- Capital raid weekends.
CREATE TABLE IF NOT EXISTS raid_seasons (
    clan_tag        TEXT NOT NULL,
    start_time      TEXT NOT NULL,
    end_time        TEXT NOT NULL,
    state           TEXT NOT NULL,
    total_loot      INTEGER,
    attack_count    INTEGER,
    districts_destroyed INTEGER,
    offensive_reward INTEGER,
    defensive_reward INTEGER,
    PRIMARY KEY (clan_tag, start_time)
);

CREATE TABLE IF NOT EXISTS raid_members (
    clan_tag     TEXT    NOT NULL,
    start_time   TEXT    NOT NULL,
    player_tag   TEXT    NOT NULL,
    name         TEXT    NOT NULL,
    attacks      INTEGER NOT NULL,
    attack_limit INTEGER NOT NULL,
    loot         INTEGER NOT NULL,
    PRIMARY KEY (clan_tag, start_time, player_tag)
);

-- Last observed state per entity so diffs survive restarts.
CREATE TABLE IF NOT EXISTS snapshot_cache (
    key        TEXT PRIMARY KEY,        -- e.g. "clan:#TAG", "player:#TAG"
    data       TEXT NOT NULL,           -- JSON
    updated_at TEXT NOT NULL
);
