-- Idempotent data fixes, run on every start after the schema and added columns are in place.

-- Ranked battles score by stars and destruction (services/battlelog.attack_trophies; keep the two
-- in step): 3 stars earn 40; 2 stars 16 at 50% plus 1 per full 3% above; 1 star 5 at 1% plus 1
-- per full 9% above; 0 stars (below Legend I) 8% of the destruction. Re-applied to stored battles
-- so rows saved under an older rule are corrected.

-- Below Legend I (battle_type 'ranked'): the attacker's gain; the defender gains what's left of 40.
UPDATE legend_events
SET trophies = CASE WHEN kind = 'attack' THEN 0 ELSE 40 END + (CASE WHEN kind = 'attack' THEN 1 ELSE -1 END) * (
    CASE stars
        WHEN 3 THEN 40
        WHEN 2 THEN 16 + MAX(CAST(destruction AS INTEGER) - 50, 0) / 3
        WHEN 1 THEN 5 + MAX(CAST(destruction AS INTEGER) - 1, 0) / 9
        ELSE CAST(destruction * 0.08 + 0.5 AS INTEGER)
    END)
WHERE battle_type = 'ranked' AND stars BETWEEN 0 AND 3 AND destruction IS NOT NULL;

-- Legend I (battle_type 'legend'): the defender loses what the attacker earns.
UPDATE legend_events
SET trophies = (CASE WHEN kind = 'attack' THEN 1 ELSE -1 END) * (
    CASE stars
        WHEN 3 THEN 40
        WHEN 2 THEN 16 + MAX(CAST(destruction AS INTEGER) - 50, 0) / 3
        ELSE 5 + MAX(CAST(destruction AS INTEGER) - 1, 0) / 9
    END)
WHERE battle_type = 'legend' AND stars BETWEEN 1 AND 3 AND destruction IS NOT NULL;
