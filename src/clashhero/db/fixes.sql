-- Idempotent data fixes, run on every start after the schema and added columns are in place.

-- Battles below Legend I (battle_type 'ranked') score by stars and destruction: the attacker earns
-- 8/20/32/40% of the destruction for 0/1/2/3 stars, the defender gains what's left of 40. Rows
-- stored before this rule existed have 0. Same rule as services/battlelog.ranked_trophies.
UPDATE legend_events
SET trophies = CASE WHEN kind = 'attack'
        THEN CAST(destruction * (CASE stars WHEN 0 THEN 0.08 WHEN 1 THEN 0.20 WHEN 2 THEN 0.32 ELSE 0.40 END) + 0.5 AS INTEGER)
        ELSE 40 - CAST(destruction * (CASE stars WHEN 0 THEN 0.08 WHEN 1 THEN 0.20 WHEN 2 THEN 0.32 ELSE 0.40 END) + 0.5 AS INTEGER)
    END
WHERE battle_type = 'ranked' AND stars BETWEEN 0 AND 3 AND destruction IS NOT NULL;
