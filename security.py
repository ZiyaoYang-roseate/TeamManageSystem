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
    """Private salary plans and draft boards belong only to the logged-in team."""
    return management_team_id()
