CREATE TABLE IF NOT EXISTS scorekeeper_business_dates (
    season VARCHAR(9) PRIMARY KEY,
    business_date DATE NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO scorekeeper_business_dates (season, business_date)
SELECT
    season,
    COALESCE(
        MIN(CASE WHEN status <> 'finished' THEN game_date END),
        MAX(game_date)
    ) AS business_date
FROM games
GROUP BY season;
