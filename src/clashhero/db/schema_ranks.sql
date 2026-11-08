-- Global player ranks (repos/ranks.py). The Clash API only publishes the global top 200, so only
-- players in it have a rank here; everyone else simply has no row.

-- The latest fetch of the global ranking (replaced as a whole each time).
CREATE TABLE IF NOT EXISTS player_ranks (
    player_tag TEXT    PRIMARY KEY,
    rank       INTEGER NOT NULL,
    trophies   INTEGER NOT NULL,
    fetched_at TEXT    NOT NULL
);

-- Rank per legend day: overwritten by every fetch during the day, so after the 05:00 UTC reset it
-- holds the rank from the last fetch before it (the end-of-day rank).
CREATE TABLE IF NOT EXISTS legend_day_ranks (
    player_tag TEXT    NOT NULL,
    legend_day TEXT    NOT NULL,
    rank       INTEGER NOT NULL,
    trophies   INTEGER NOT NULL,
    PRIMARY KEY (player_tag, legend_day)
);

-- Country leaderboards: each country's top 200 (players who set that country in-game). A player
-- is in at most one country's ranking; replaced country by country on each fetch.
CREATE TABLE IF NOT EXISTS player_country_ranks (
    player_tag   TEXT    PRIMARY KEY,
    location_id  INTEGER NOT NULL,
    country_code TEXT    NOT NULL,
    country_name TEXT    NOT NULL,
    rank         INTEGER NOT NULL,
    trophies     INTEGER NOT NULL,
    fetched_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_country_ranks_location ON player_country_ranks (location_id);
