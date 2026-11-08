-- War target calling ("/caller"). One caller per enemy base position per war.
CREATE TABLE IF NOT EXISTS war_callers (
    war_id      INTEGER NOT NULL REFERENCES wars (id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,          -- enemy map position
    player_tag  TEXT,                      -- our attacker (if a Clash account was given)
    user_id     INTEGER,                   -- Discord user who claimed it
    note        TEXT,
    assigned_by INTEGER NOT NULL,
    assigned_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (war_id, position)
);

-- Target calls are private to the server that made them: a server tracking the *opposing* clan
-- must never see or change them. (Replaces war_callers, which was shared by every server
-- tracking the same clan.)
CREATE TABLE IF NOT EXISTS war_calls (
    guild_id    INTEGER NOT NULL,
    war_id      INTEGER NOT NULL REFERENCES wars (id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,          -- enemy map position
    player_tag  TEXT,                      -- our attacker (if a Clash account was given)
    user_id     INTEGER,                   -- Discord user who claimed it
    note        TEXT,
    assigned_by INTEGER NOT NULL,
    assigned_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (guild_id, war_id, position)
);

-- One-time move of calls saved in the old shared table: each goes to the server tracking that
-- clan, only when exactly one server does (otherwise the owner can't be known, so it's dropped).
INSERT OR IGNORE INTO war_calls (guild_id, war_id, position, player_tag, user_id, note, assigned_by, assigned_at)
SELECT g.guild_id, c.war_id, c.position, c.player_tag, c.user_id, c.note, c.assigned_by, c.assigned_at
FROM war_callers c
JOIN wars w ON w.id = c.war_id
JOIN guild_clans g ON g.clan_tag = w.clan_tag
WHERE (SELECT COUNT(*) FROM guild_clans g2 WHERE g2.clan_tag = w.clan_tag) = 1;
DELETE FROM war_callers;
