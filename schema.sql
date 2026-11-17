DROP TABLE IF EXISTS game_analysis_reports;
DROP TABLE IF EXISTS game_analysis_snapshots;
DROP TABLE IF EXISTS game_player_availability;
DROP TABLE IF EXISTS game_starters;
DROP TABLE IF EXISTS trade_proposals;
DROP TABLE IF EXISTS schema_migrations;
DROP TABLE IF EXISTS scorekeeper_business_dates;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS draft_picks;
DROP TABLE IF EXISTS contracts;
DROP TABLE IF EXISTS contract_offers;
DROP TABLE IF EXISTS free_agency_targets;
DROP TABLE IF EXISTS free_agent_signing_history;
DROP TABLE IF EXISTS trade_history;
DROP TABLE IF EXISTS scout_reports;
DROP TABLE IF EXISTS player_health;
DROP TABLE IF EXISTS play_by_play;
DROP TABLE IF EXISTS salary_caps;
DROP TABLE IF EXISTS lineup_combinations;
DROP TABLE IF EXISTS shot_attempts;
DROP TABLE IF EXISTS player_game_stats;
DROP TABLE IF EXISTS games;
DROP TABLE IF EXISTS players;
DROP TABLE IF EXISTS coaches;
DROP TABLE IF EXISTS teams;

CREATE TABLE teams (
    id INT AUTO_INCREMENT PRIMARY KEY,
    city VARCHAR(50) NOT NULL,
    name VARCHAR(80) NOT NULL,
    abbreviation VARCHAR(3) NOT NULL UNIQUE,
    founded_year SMALLINT NOT NULL,
    arena VARCHAR(100) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL,
    team_id INT NULL,
    display_name VARCHAR(50) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_users_team FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE,
    UNIQUE KEY uq_users_management_team (team_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE coaches (
    id INT AUTO_INCREMENT PRIMARY KEY,
    team_id INT NOT NULL UNIQUE,
    name VARCHAR(80) NOT NULL,
    nationality VARCHAR(50) NOT NULL,
    experience_years INT NOT NULL,
    CONSTRAINT fk_coaches_team FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE players (
    id INT AUTO_INCREMENT PRIMARY KEY,
    team_id INT NULL,
    name VARCHAR(80) NOT NULL,
    jersey_number INT NOT NULL,
    position VARCHAR(20) NOT NULL,
    age INT NOT NULL,
    height_cm DECIMAL(5, 2) NOT NULL,
    weight_kg DECIMAL(5, 2) NOT NULL,
    nationality VARCHAR(50) NOT NULL,
    salary INT NOT NULL,
    CONSTRAINT fk_players_team FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE SET NULL,
    UNIQUE KEY uq_team_jersey (team_id, jersey_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE games (
    id INT AUTO_INCREMENT PRIMARY KEY,
    season VARCHAR(9) NOT NULL,
    game_date DATE NOT NULL,
    home_team_id INT NOT NULL,
    away_team_id INT NOT NULL,
    home_score INT NOT NULL,
    away_score INT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'scheduled',
    starters_initialized TINYINT(1) NOT NULL DEFAULT 0,
    period INT NOT NULL DEFAULT 1,
    seconds_remaining INT NOT NULL DEFAULT 720,
    INDEX idx_games_season_date (season, game_date, id),
    CONSTRAINT fk_games_home FOREIGN KEY (home_team_id) REFERENCES teams (id) ON DELETE CASCADE,
    CONSTRAINT fk_games_away FOREIGN KEY (away_team_id) REFERENCES teams (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE scorekeeper_business_dates (
    season VARCHAR(9) PRIMARY KEY,
    business_date DATE NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE player_game_stats (
    id INT AUTO_INCREMENT PRIMARY KEY,
    game_id INT NOT NULL,
    player_id INT NOT NULL,
    team_id INT NOT NULL,
    minutes_played INT NOT NULL,
    points INT NOT NULL,
    rebounds INT NOT NULL,
    assists INT NOT NULL,
    steals INT NOT NULL,
    blocks INT NOT NULL,
    turnovers INT NOT NULL,
    fouls INT NOT NULL DEFAULT 0,
    seconds_played INT NOT NULL DEFAULT 0,
    is_on_court TINYINT(1) NOT NULL DEFAULT 0,
    field_goals_made INT NOT NULL,
    field_goals_attempted INT NOT NULL,
    three_points_made INT NOT NULL,
    free_throws_made INT NOT NULL,
    free_throws_attempted INT NOT NULL,
    plus_minus INT NOT NULL,
    CONSTRAINT fk_stats_game FOREIGN KEY (game_id) REFERENCES games (id) ON DELETE CASCADE,
    CONSTRAINT fk_stats_player FOREIGN KEY (player_id) REFERENCES players (id) ON DELETE CASCADE,
    CONSTRAINT fk_stats_team FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE,
    UNIQUE KEY uq_game_player (game_id, player_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE shot_attempts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    game_id INT NOT NULL,
    player_id INT NOT NULL,
    team_id INT NOT NULL,
    period INT NOT NULL,
    seconds_remaining INT NOT NULL,
    x DECIMAL(5, 2) NOT NULL,
    y DECIMAL(5, 2) NOT NULL,
    distance_cm INT NOT NULL,
    shot_type VARCHAR(30) NOT NULL,
    zone VARCHAR(40) NOT NULL,
    made TINYINT(1) NOT NULL,
    voided_at TIMESTAMP NULL,
    CONSTRAINT fk_shots_game FOREIGN KEY (game_id) REFERENCES games (id) ON DELETE CASCADE,
    CONSTRAINT fk_shots_player FOREIGN KEY (player_id) REFERENCES players (id) ON DELETE CASCADE,
    CONSTRAINT fk_shots_team FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE lineup_combinations (
    id INT AUTO_INCREMENT PRIMARY KEY,
    team_id INT NOT NULL,
    combination_size INT NOT NULL,
    player_ids VARCHAR(255) NOT NULL,
    minutes_played INT NOT NULL,
    possessions INT NOT NULL,
    points_for INT NOT NULL,
    points_against INT NOT NULL,
    assists INT NOT NULL,
    turnovers INT NOT NULL,
    off_rating DECIMAL(6, 2) NOT NULL,
    def_rating DECIMAL(6, 2) NOT NULL,
    net_rating DECIMAL(7, 2) NOT NULL,
    CONSTRAINT fk_lineups_team FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE salary_caps (
    id INT AUTO_INCREMENT PRIMARY KEY,
    season VARCHAR(9) NOT NULL UNIQUE,
    cap_amount INT NOT NULL,
    luxury_tax_line INT NOT NULL,
    first_apron INT NOT NULL,
    second_apron INT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE contracts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    player_id INT NOT NULL,
    team_id INT NOT NULL,
    start_year SMALLINT NOT NULL,
    end_year SMALLINT NOT NULL,
    annual_salary INT NOT NULL,
    annual_increase_pct DECIMAL(5,4) NOT NULL DEFAULT 0,
    source_offer_id INT NULL,
    contract_type VARCHAR(30) NOT NULL,
    player_option TINYINT(1) NOT NULL,
    team_option TINYINT(1) NOT NULL,
    incentives INT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'Active',
    active_player_id INT GENERATED ALWAYS AS (
        CASE WHEN status = 'Active' THEN player_id ELSE NULL END
    ) VIRTUAL,
    UNIQUE KEY uq_contract_active_player (active_player_id),
    INDEX idx_contract_player (player_id),
    CONSTRAINT fk_contracts_player FOREIGN KEY (player_id) REFERENCES players (id) ON DELETE CASCADE,
    CONSTRAINT fk_contracts_team FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE free_agent_signing_history (
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

CREATE TABLE play_by_play (
    id INT AUTO_INCREMENT PRIMARY KEY,
    game_id INT NOT NULL,
    period INT NOT NULL,
    seconds_remaining INT NOT NULL,
    team_id INT NOT NULL,
    player_id INT NULL,
    assist_player_id INT NULL,
    event_type VARCHAR(40) NOT NULL,
    description VARCHAR(255) NOT NULL,
    score_home INT NOT NULL,
    score_away INT NOT NULL,
    request_id CHAR(36) NULL,
    request_hash CHAR(64) NULL,
    recording JSON NULL,
    voided_at TIMESTAMP NULL,
    voided_by INT NULL,
    correction_reason VARCHAR(255) NULL,
    UNIQUE KEY uq_pbp_request (game_id, request_id),
    CONSTRAINT fk_pbp_game FOREIGN KEY (game_id) REFERENCES games (id) ON DELETE CASCADE,
    CONSTRAINT fk_pbp_team FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE,
    CONSTRAINT fk_pbp_player FOREIGN KEY (player_id) REFERENCES players (id) ON DELETE SET NULL,
    CONSTRAINT fk_pbp_assist FOREIGN KEY (assist_player_id) REFERENCES players (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE player_health (
    id INT AUTO_INCREMENT PRIMARY KEY,
    player_id INT NOT NULL UNIQUE,
    injury_status VARCHAR(20) NOT NULL,
    injury_description VARCHAR(100) NOT NULL,
    consecutive_games INT NOT NULL,
    avg_minutes DECIMAL(5, 1) NOT NULL,
    fatigue_score INT NOT NULL,
    load_warning VARCHAR(50) NOT NULL,
    CONSTRAINT fk_health_player FOREIGN KEY (player_id) REFERENCES players (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE game_player_availability (
    game_id INT NOT NULL,
    player_id INT NOT NULL,
    team_id INT NOT NULL,
    status VARCHAR(20) NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (game_id, player_id),
    KEY idx_availability_game_team (game_id, team_id),
    CONSTRAINT fk_availability_game FOREIGN KEY (game_id) REFERENCES games (id) ON DELETE CASCADE,
    CONSTRAINT fk_availability_player FOREIGN KEY (player_id) REFERENCES players (id) ON DELETE CASCADE,
    CONSTRAINT fk_availability_team FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE game_starters (
    game_id INT NOT NULL,
    player_id INT NOT NULL,
    team_id INT NOT NULL,
    PRIMARY KEY (game_id, player_id),
    KEY idx_starters_game_team (game_id, team_id),
    CONSTRAINT fk_starters_game FOREIGN KEY (game_id) REFERENCES games (id) ON DELETE CASCADE,
    CONSTRAINT fk_starters_player FOREIGN KEY (player_id) REFERENCES players (id) ON DELETE CASCADE,
    CONSTRAINT fk_starters_team FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE scout_reports (
    id INT AUTO_INCREMENT PRIMARY KEY,
    player_id INT NOT NULL UNIQUE,
    scoring INT NOT NULL,
    defense INT NOT NULL,
    playmaking INT NOT NULL,
    athleticism INT NOT NULL,
    basketball_iq INT NOT NULL,
    overall_rating INT NOT NULL,
    notes TEXT NOT NULL,
    CONSTRAINT fk_scout_player FOREIGN KEY (player_id) REFERENCES players (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE draft_picks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    team_id INT NOT NULL,
    season VARCHAR(9) NOT NULL,
    round TINYINT NOT NULL,
    pick_number INT NOT NULL,
    protection VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL,
    CONSTRAINT fk_draft_team FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE free_agency_targets (
    id INT AUTO_INCREMENT PRIMARY KEY,
    player_id INT NOT NULL,
    team_id INT NULL,
    tier VARCHAR(20) NOT NULL,
    priority_order INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_target_team_player (team_id, player_id),
    CONSTRAINT fk_target_team FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE,
    CONSTRAINT fk_target_player FOREIGN KEY (player_id) REFERENCES players (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE contract_offers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    target_id INT NOT NULL,
    season VARCHAR(9) NULL,
    exception_type VARCHAR(30) NOT NULL DEFAULT 'Standard',
    version INT NOT NULL DEFAULT 1,
    years INT NOT NULL,
    year1_salary INT NOT NULL,
    annual_increase_pct DECIMAL(5, 4) NOT NULL,
    team_option TINYINT(1) NOT NULL DEFAULT 0,
    player_option TINYINT(1) NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'Draft',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_offer_target FOREIGN KEY (target_id) REFERENCES free_agency_targets (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE trade_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trade_date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    team1_id INT NOT NULL,
    team2_id INT NOT NULL,
    team1_outgoing TEXT NOT NULL,
    team2_outgoing TEXT NOT NULL,
    CONSTRAINT fk_trade_team1 FOREIGN KEY (team1_id) REFERENCES teams (id) ON DELETE CASCADE,
    CONSTRAINT fk_trade_team2 FOREIGN KEY (team2_id) REFERENCES teams (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE trade_proposals (
    id INT AUTO_INCREMENT PRIMARY KEY,
    proposer_team_id INT NOT NULL,
    receiver_team_id INT NOT NULL,
    proposer_user_id INT NOT NULL,
    proposer_outgoing TEXT NOT NULL,
    receiver_outgoing TEXT NOT NULL,
    season VARCHAR(9) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    responded_at TIMESTAMP NULL,
    CONSTRAINT fk_trade_proposal_proposer_team FOREIGN KEY (proposer_team_id) REFERENCES teams (id) ON DELETE CASCADE,
    CONSTRAINT fk_trade_proposal_receiver_team FOREIGN KEY (receiver_team_id) REFERENCES teams (id) ON DELETE CASCADE,
    CONSTRAINT fk_trade_proposal_user FOREIGN KEY (proposer_user_id) REFERENCES users (id) ON DELETE CASCADE,
    INDEX idx_trade_proposal_proposer (proposer_team_id, status, created_at),
    INDEX idx_trade_proposal_receiver (receiver_team_id, status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE game_analysis_snapshots (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    game_id INT NOT NULL,
    team_id INT NOT NULL,
    kind VARCHAR(12) NOT NULL,
    fingerprint CHAR(64) NOT NULL,
    metrics_version VARCHAR(20) NOT NULL,
    evidence JSON NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_analysis_snapshot (game_id, team_id, kind, fingerprint),
    CONSTRAINT fk_analysis_snapshot_game FOREIGN KEY (game_id) REFERENCES games (id) ON DELETE CASCADE,
    CONSTRAINT fk_analysis_snapshot_team FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE game_analysis_reports (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    snapshot_id BIGINT NOT NULL,
    generator VARCHAR(100) NOT NULL,
    prompt_version VARCHAR(20) NOT NULL,
    source VARCHAR(12) NULL,
    content JSON NULL,
    attempt_token CHAR(32) NOT NULL,
    lease_until DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP NULL,
    UNIQUE KEY uq_analysis_report (snapshot_id, generator, prompt_version),
    CONSTRAINT fk_analysis_report_snapshot FOREIGN KEY (snapshot_id) REFERENCES game_analysis_snapshots (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
