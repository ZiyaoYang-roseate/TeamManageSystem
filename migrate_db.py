import argparse
import re
from pathlib import Path

import pymysql

from db import DB_CONFIG


def create_database():
    name = DB_CONFIG["database"]
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError("MYSQL_DATABASE 只能包含字母、数字和下划线")
    with pymysql.connect(**{k: v for k, v in DB_CONFIG.items() if k != "database"}) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{name}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )


def schema_statements():
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    return [statement.strip() for statement in schema.split(";") if statement.strip()]


def add_column(cursor, table, name, definition):
    cursor.execute(f"SHOW COLUMNS FROM `{table}` WHERE Field = %s", (name,))
    if cursor.fetchone() is None:
        cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN `{name}` {definition}")
        return True
    return False


def add_index(cursor, table, name, definition):
    cursor.execute(f"SHOW INDEX FROM `{table}` WHERE Key_name = %s", (name,))
    if cursor.fetchone() is None:
        cursor.execute(f"ALTER TABLE `{table}` ADD {definition}")


def drop_player_uniqueness(cursor, table, replacement_index):
    cursor.execute(f"SHOW INDEX FROM `{table}`")
    unique_indexes = {}
    for index in cursor.fetchall():
        if index["Non_unique"] == 0:
            unique_indexes.setdefault(index["Key_name"], []).append(index["Column_name"])
    for name, columns in unique_indexes.items():
        if columns == ["player_id"]:
            add_index(cursor, table, replacement_index, f"INDEX {replacement_index} (player_id)")
            name = name.replace("`", "``")
            cursor.execute(f"ALTER TABLE `{table}` DROP INDEX `{name}`")


def prepare_schema(cursor):
    # The schema's DROP statements are reserved for the explicit demo reset.
    for statement in schema_statements():
        if statement.startswith("CREATE TABLE ") and not statement.startswith("CREATE TABLE game_analysis_"):
            cursor.execute(statement.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1))

    add_column(cursor, "users", "team_id", "INT NULL")
    add_index(cursor, "users", "uq_users_management_team", "UNIQUE KEY uq_users_management_team (team_id)")
    cursor.execute(
        """
        SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS
        WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'users'
          AND CONSTRAINT_NAME = 'fk_users_team'
        """
    )
    if cursor.fetchone() is None:
        cursor.execute(
            "ALTER TABLE users ADD CONSTRAINT fk_users_team "
            "FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE"
        )
    add_column(cursor, "games", "status", "VARCHAR(20) NULL")
    add_column(cursor, "games", "period", "INT NOT NULL DEFAULT 1")
    add_column(cursor, "games", "seconds_remaining", "INT NOT NULL DEFAULT 720")
    add_column(cursor, "player_game_stats", "fouls", "INT NOT NULL DEFAULT 0")
    add_column(cursor, "player_game_stats", "seconds_played", "INT NOT NULL DEFAULT 0")
    add_column(cursor, "player_game_stats", "is_on_court", "TINYINT(1) NOT NULL DEFAULT 0")
    cursor.execute("SHOW COLUMNS FROM games WHERE Field = 'status'")
    if cursor.fetchone()["Null"] == "YES":
        cursor.execute(
            """
            UPDATE games g SET status = 'finished', period = 4, seconds_remaining = 0
            WHERE g.status IS NULL AND (
                home_score <> 0 OR away_score <> 0
                OR EXISTS (SELECT 1 FROM play_by_play e WHERE e.game_id = g.id)
                OR EXISTS (SELECT 1 FROM player_game_stats s WHERE s.game_id = g.id)
                OR EXISTS (SELECT 1 FROM shot_attempts a WHERE a.game_id = g.id)
            )
            """
        )
        cursor.execute("UPDATE games SET status = 'scheduled' WHERE status IS NULL")
        cursor.execute("ALTER TABLE games MODIFY status VARCHAR(20) NOT NULL DEFAULT 'scheduled'")


def migrate_reliability(cursor):
    add_index(cursor, "games", "idx_games_season_date", "INDEX idx_games_season_date (season, game_date, id)")
    add_column(cursor, "play_by_play", "request_id", "CHAR(36) NULL")
    add_column(cursor, "play_by_play", "request_hash", "CHAR(64) NULL")
    add_index(cursor, "play_by_play", "uq_pbp_request", "UNIQUE KEY uq_pbp_request (game_id, request_id)")
    add_column(cursor, "free_agency_targets", "team_id", "INT NULL")
    add_index(cursor, "free_agency_targets", "uq_target_team_player", "UNIQUE KEY uq_target_team_player (team_id, player_id)")
    cursor.execute(
        """
        SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS
        WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'free_agency_targets'
          AND CONSTRAINT_NAME = 'fk_target_team'
        """
    )
    if cursor.fetchone() is None:
        cursor.execute(
            "ALTER TABLE free_agency_targets ADD CONSTRAINT fk_target_team "
            "FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE"
        )
    # Preserve the player foreign key's supporting index before dropping its global uniqueness.
    drop_player_uniqueness(cursor, "free_agency_targets", "idx_target_player")
    add_column(
        cursor, "contracts", "active_player_id",
        "INT GENERATED ALWAYS AS (CASE WHEN status = 'Active' THEN player_id ELSE NULL END) VIRTUAL",
    )
    add_index(cursor, "contracts", "uq_contract_active_player", "UNIQUE KEY uq_contract_active_player (active_player_id)")
    drop_player_uniqueness(cursor, "contracts", "idx_contract_player")


def populate_business_dates(cursor):
    cursor.execute(
        """
        INSERT IGNORE INTO scorekeeper_business_dates (season, business_date)
        SELECT season,
               COALESCE(MIN(CASE WHEN status IN ('scheduled', 'live') THEN game_date END), MAX(game_date))
        FROM games GROUP BY season
        """
    )


def migrate_game_analysis(cursor):
    for statement in schema_statements():
        if statement.startswith("CREATE TABLE game_analysis_"):
            cursor.execute(statement.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1))


def migrate_pregame(cursor):
    for statement in schema_statements():
        if statement.startswith(("CREATE TABLE game_player_availability ", "CREATE TABLE game_starters ")):
            cursor.execute(statement.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1))


def migrate_starter_gate(cursor):
    add_column(cursor, "games", "starters_initialized", "TINYINT(1) NOT NULL DEFAULT 0")
    cursor.execute(
        """
        UPDATE games g
        SET g.starters_initialized = 1
        WHERE g.status = 'live'
          AND (SELECT COUNT(*) FROM game_starters s WHERE s.game_id = g.id AND s.team_id = g.home_team_id) = 5
          AND (SELECT COUNT(*) FROM game_starters s WHERE s.game_id = g.id AND s.team_id = g.away_team_id) = 5
        """
    )


def migrate_confirmed_contracts_and_event_corrections(cursor):
    for table, column, definition in (
        ("contracts", "annual_increase_pct", "DECIMAL(5,4) NOT NULL DEFAULT 0"),
        ("contracts", "source_offer_id", "INT NULL"),
        ("contract_offers", "season", "VARCHAR(9) NULL"),
        ("contract_offers", "exception_type", "VARCHAR(30) NOT NULL DEFAULT 'Standard'"),
        ("contract_offers", "version", "INT NOT NULL DEFAULT 1"),
        ("play_by_play", "recording", "JSON NULL"),
        ("play_by_play", "voided_at", "TIMESTAMP NULL"),
        ("play_by_play", "voided_by", "INT NULL"),
        ("play_by_play", "correction_reason", "VARCHAR(255) NULL"),
        ("shot_attempts", "voided_at", "TIMESTAMP NULL"),
    ):
        add_column(cursor, table, column, definition)
    cursor.execute(
        """
        SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS
        WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'contracts'
          AND CONSTRAINT_NAME = 'fk_contract_confirmed_offer'
        """
    )
    if cursor.fetchone() is None:
        cursor.execute(
            "ALTER TABLE contracts ADD CONSTRAINT fk_contract_confirmed_offer "
            "FOREIGN KEY (source_offer_id) REFERENCES contract_offers (id) ON DELETE SET NULL"
        )


def migrate_team_planning(cursor):
    tables = {"draft_prospects", "draft_board", "league_game_reviews"}
    for statement in schema_statements():
        if statement.startswith("CREATE TABLE ") and statement.split()[2] in tables:
            cursor.execute(statement.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1))


def migrate_archive(cursor):
    add_column(cursor, "players", "archived_at", "TIMESTAMP NULL")
    add_column(cursor, "teams", "archived_at", "TIMESTAMP NULL")


def migrate(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT GET_LOCK(CONCAT('nba-migrate:', LEFT(SHA2(DATABASE(), 256), 48)), 10) AS acquired")
        if cursor.fetchone()["acquired"] != 1:
            raise RuntimeError("另一个进程正在迁移数据库")
        try:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INT PRIMARY KEY,
                    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB
                """
            )
            cursor.execute("SELECT version FROM schema_migrations")
            installed = {row["version"] for row in cursor.fetchall()}
            for version, upgrade in [
                (1, prepare_schema), (2, migrate_reliability),
                (3, migrate_game_analysis), (4, migrate_pregame),
                (5, migrate_starter_gate),
                (6, migrate_confirmed_contracts_and_event_corrections),
                (7, migrate_team_planning),
                (8, migrate_archive),
            ]:
                if version not in installed:
                    upgrade(cursor)
                    cursor.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
                    connection.commit()
            populate_business_dates(cursor)
            connection.commit()
        finally:
            cursor.execute("SELECT RELEASE_LOCK(CONCAT('nba-migrate:', LEFT(SHA2(DATABASE(), 256), 48)))")


def main():
    parser = argparse.ArgumentParser(description="创建或升级数据库，保留现有业务记录")
    parser.add_argument("--create-database", action="store_true")
    args = parser.parse_args()
    if args.create_database:
        create_database()
    with pymysql.connect(**DB_CONFIG) as connection:
        migrate(connection)
    print("数据库迁移完成")


if __name__ == "__main__":
    main()
