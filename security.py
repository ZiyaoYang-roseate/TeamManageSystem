import secrets
from hmac import compare_digest

from flask import abort, g, request, session

from auth import ROLE_LEAGUE_ADMIN, ROLE_MANAGEMENT


def csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]


def require_csrf():
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("_csrf_token", "")
    expected = session.get("_csrf_token")
    if not expected or not supplied.isascii() or not compare_digest(expected, supplied):
        abort(403, "请求凭据无效，请刷新页面后重试")


def can_edit_team(team_id):
    user = getattr(g, "user", None)
    return bool(user and (
        user["role"] == ROLE_LEAGUE_ADMIN
        or (user["role"] == ROLE_MANAGEMENT and team_id is not None and user["team_id"] == team_id)
    ))


def require_league_admin():
    if g.user["role"] != ROLE_LEAGUE_ADMIN:
        abort(403)


def management_team_id():
    user = getattr(g, "user", None)
    if not user or user["role"] != ROLE_MANAGEMENT or user["team_id"] is None:
        abort(403)
    return int(user["team_id"])


def planning_team_id():
    """Team whose planning workspace (salary / draft board) should be shown.

    Managers always land on their own team in edit mode. League admins have no
    team of their own, so they get a read-only inspection view of any team,
    picked with ``?team_id=`` and defaulting to the lowest team id.
    """
    user = getattr(g, "user", None)
    if not user:
        abort(403)
    if user["role"] == ROLE_MANAGEMENT and user["team_id"] is not None:
        g.planning_readonly = False
        return int(user["team_id"])
    if user["role"] != ROLE_LEAGUE_ADMIN:
        abort(403)

    from db import fetch_all

    g.planning_readonly = True
    rows = fetch_all("SELECT id FROM teams ORDER BY id")
    known = [int(row["id"]) for row in rows]
    if not known:
        abort(403)
    wanted = request.args.get("team_id", type=int)
    return wanted if wanted in known else known[0]


def planning_teams():
    """Selectable teams for the league-admin inspection view."""
    from db import fetch_all

    return fetch_all(
        "SELECT id, city, name, abbreviation FROM teams ORDER BY city, name"
    )
