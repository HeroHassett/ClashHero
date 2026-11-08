-- Legends / ranked tracking (cogs/legends.py).

-- Battle alert subscriptions (/track).
-- channel_id = 0 means "DM the subscriber".
CREATE TABLE IF NOT EXISTS legend_subscriptions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER,                 -- guild the subscription was created in (NULL in DMs)
    user_id      INTEGER NOT NULL,        -- subscriber (DM recipient / owner)
    player_tag   TEXT    NOT NULL,
    channel_id   INTEGER NOT NULL DEFAULT 0,
    attacks      INTEGER NOT NULL DEFAULT 1,
    defenses     INTEGER NOT NULL DEFAULT 1,
    ping_role_id INTEGER,                 -- pinged on defense alerts (channel destinations only)
    created_by   INTEGER NOT NULL,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (player_tag, user_id, channel_id)
);
CREATE INDEX IF NOT EXISTS idx_legend_subs_player ON legend_subscriptions (player_tag);

-- Players shown on a guild's live legend leaderboard.
CREATE TABLE IF NOT EXISTS legend_board_players (
    guild_id   INTEGER NOT NULL,
    player_tag TEXT    NOT NULL,
    added_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (guild_id, player_tag)
);

-- Dedupe for the "legend_daily" clan log.
CREATE TABLE IF NOT EXISTS legend_daily_posted (
    clan_tag   TEXT NOT NULL,
    legend_day TEXT NOT NULL,
    PRIMARY KEY (clan_tag, legend_day)
);
