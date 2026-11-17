import os

from werkzeug.security import check_password_hash

from db import fetch_one


ROLE_MANAGEMENT = "management"
ROLE_LEAGUE_ADMIN = "league_admin"
ROLE_SCOREKEEPER = "scorekeeper"

ROLE_LABELS = {
    ROLE_MANAGEMENT: "管理层",
    ROLE_LEAGUE_ADMIN: "联赛管理员",
    ROLE_SCOREKEEPER: "记分员",
}

def get_user_by_id(user_id):
    return fetch_one(
        """
        SELECT u.id, u.username, u.role, u.team_id, u.display_name,
               t.city AS team_city, t.name AS team_name,
               t.abbreviation AS team_abbreviation
        FROM users u
        LEFT JOIN teams t ON t.id = u.team_id
        WHERE u.id = %s
        """,
        (user_id,),
    )


def get_user_by_username(username):
    return fetch_one(
        """
        SELECT u.id, u.username, u.password_hash, u.role, u.team_id,
               u.display_name, t.city AS team_city, t.name AS team_name,
               t.abbreviation AS team_abbreviation
        FROM users u
        LEFT JOIN teams t ON t.id = u.team_id
        WHERE u.username = %s
        """,
        (username,),
    )


def authenticate_user(username, password):
    demo_password = os.getenv("NBA_DEMO_PASSWORD")
    if len(password) < 12 and (not demo_password or password != demo_password):
        return None
    user = get_user_by_username(username)
    if user is None or not check_password_hash(user["password_hash"], password):
        return None
    return user
