-- Run these statements once against an existing database that was created
-- before the scorekeeper module was added. The updated schema.sql already
-- includes both columns for fresh installations.

ALTER TABLE games
    ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'scheduled'
    AFTER away_score;

ALTER TABLE games
    ADD COLUMN period INT NOT NULL DEFAULT 1
    AFTER status,
    ADD COLUMN seconds_remaining INT NOT NULL DEFAULT 720
    AFTER period;

ALTER TABLE player_game_stats
    ADD COLUMN fouls INT NOT NULL DEFAULT 0
    AFTER turnovers,
    ADD COLUMN seconds_played INT NOT NULL DEFAULT 0
    AFTER fouls,
    ADD COLUMN is_on_court TINYINT(1) NOT NULL DEFAULT 0
    AFTER seconds_played;

-- Reset the whole schedule to clean, unstarted matches. Existing generated
-- stats and events are removed so standings and player metrics start empty.
DELETE FROM play_by_play;
DELETE FROM player_game_stats;
DELETE FROM shot_attempts;
UPDATE games
SET home_score = 0,
    away_score = 0,
    status = 'scheduled',
    period = 1,
    seconds_remaining = 720;

-- To open a specific game for scorekeeping, remove its old generated data
-- and reset it to a clean scheduled match with a 0:0 score.
-- Example for game id 85:
-- DELETE FROM play_by_play WHERE game_id = 85;
-- DELETE FROM player_game_stats WHERE game_id = 85;
-- DELETE FROM shot_attempts WHERE game_id = 85;
-- UPDATE games SET home_score = 0, away_score = 0, status = 'scheduled' WHERE id = 85;
