-- Existing databases can run this migration without rebuilding demo data.
CREATE TABLE IF NOT EXISTS free_agent_signing_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    signed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    player_id INT NULL,
    team_id INT NOT NULL,
    player_name VARCHAR(80) NOT NULL,
    season VARCHAR(9) NOT NULL,
    contract_years INT NOT NULL,
    annual_salary INT NOT NULL,
    jersey_number INT NOT NULL,
    CONSTRAINT fk_signing_history_player FOREIGN KEY (player_id) REFERENCES players (id) ON DELETE SET NULL,
    CONSTRAINT fk_signing_history_team FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE,
    INDEX idx_signing_history_team_date (team_id, signed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
