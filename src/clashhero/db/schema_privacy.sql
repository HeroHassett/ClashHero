-- Servers the bot was found to no longer be in at startup (it may have been removed while
-- offline). Their data is purged after a grace period unless the bot rejoins.
CREATE TABLE IF NOT EXISTS guild_departures (
    guild_id   INTEGER PRIMARY KEY,
    noticed_at TEXT NOT NULL
);
