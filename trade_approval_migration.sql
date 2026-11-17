-- Adds team-bound management accounts and the bilateral trade approval workflow.
ALTER TABLE users
    ADD COLUMN team_id INT NULL AFTER role,
    ADD CONSTRAINT fk_users_team FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
    ADD UNIQUE KEY uq_users_management_team (team_id);

CREATE TABLE IF NOT EXISTS trade_proposals (
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
