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
