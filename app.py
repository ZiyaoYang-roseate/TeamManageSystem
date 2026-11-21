import json
import os
import secrets

import pymysql
from itsdangerous import BadData, URLSafeTimedSerializer
from werkzeug.exceptions import Forbidden
from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import scorekeeper
from player_directory import LEADER_METRICS, directory_data, team_members
from schedule_order import ordered_games
import team_planning
from analysis_routes import blueprint as analysis_blueprint
from auth import (
    ROLE_LEAGUE_ADMIN,
    ROLE_MANAGEMENT,
    ROLE_SCOREKEEPER,
    ROLE_LABELS,
    authenticate_user,
    get_user_by_id,
)
from db import execute, fetch_all, fetch_one, transaction
from demo_names import chinese_name, chinese_names_in_text
from draft_profiles import EXTERNAL_LEVELS, PROFILE_FIELDS, external_profile, parse_profile_fields
from game_analysis import ZONE_LABELS
from player_profiles import compare_box_scores, player_comparison_sample, player_contract, player_performance, player_career_teams
from shot_analysis import DETAIL_PAGE_SIZE, annotate_zones, recent_shooting_games, zone_shots
from decision_analysis import analyze_trade
from constants import POSITIONS, SEASONS, SHOT_ZONES, SHOT_ZONE_GEOMETRY
from caching import ttl_cache, invalidate_all
from validation import (
    BusinessError,
    bounded_float,
    bounded_int,
    choice,
    json_object,
    parse_player_form,
    parse_team_form,
    season_value,
)
from free_agency_api import blueprint as free_agency_blueprint
from league_operations import league_overview, game_operations, update_game_review
from roster import lock_player, lock_teams, require_roster_idle, season_salary_sql, sign_free_agent_with_cursor, team_payroll
from roster_packages import check_followup_after_trade, recommend_packages
from roster_recommendations import trade_recommendations
from security import (
    can_edit_team,
    csrf_token,
    management_team_id,
    planning_team_id,
    require_csrf,
    require_league_admin,
)
from trade import decode_proposal_side, encode_proposal_side, execute_trade_with_cursor, validate_trade


app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE") == "1",
    TEMPLATES_AUTO_RELOAD=True,
)
app.jinja_env.auto_reload = True
app.register_blueprint(free_agency_blueprint)
app.register_blueprint(analysis_blueprint)
app.jinja_env.filters.update(chinese_name=chinese_name, chinese_names_in_text=chinese_names_in_text)


# ---------------------------------------------------------------- formatting filters
def _as_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def fmt_n1(value):
    return f"{_as_float(value):,.1f}"


def fmt_n0(value):
    return f"{_as_float(value):,.0f}"


def fmt_signed1(value):
    number = _as_float(value)
    return f"{number:+,.1f}"


def fmt_pct1(value):
    return f"{_as_float(value) * 100:.1f}%"


def fmt_money(value):
    amount = _as_float(value)
    if abs(amount) >= 1_000_000:
        return f"${amount / 1_000_000:,.1f}M"
    if abs(amount) >= 1_000:
        return f"${amount / 1_000:,.0f}K"
    return f"${amount:,.0f}"


def fmt_clock(seconds):
    total = max(0, int(_as_float(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def fmt_md(value):
    return value.strftime("%m-%d") if hasattr(value, "strftime") else str(value or "")[5:10]


def fmt_ymd(value):
    return value.strftime("%Y-%m-%d") if hasattr(value, "strftime") else str(value or "")[:10]


_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def fmt_weekday(value):
    return _WEEKDAYS[value.weekday()] if hasattr(value, "weekday") else ""


app.jinja_env.filters.update(
    n1=fmt_n1,
    n0=fmt_n0,
    signed1=fmt_signed1,
    pct1=fmt_pct1,
    money=fmt_money,
    clock=fmt_clock,
    md=fmt_md,
    ymd=fmt_ymd,
    weekday=fmt_weekday,
)


@app.errorhandler(BusinessError)
def business_error(exc):
    if request.path.startswith("/api/"):
        return jsonify({"error": str(exc)}), exc.status_code
    return app.response_class(str(exc), status=exc.status_code, mimetype="text/plain")


@app.errorhandler(403)
def forbidden(exc):
    if request.path.startswith("/api/"):
        return jsonify({"error": exc.description}), 403
    return exc


@app.errorhandler(pymysql.err.OperationalError)
def database_error(exc):
    if exc.args[0] in (1205, 1213):
        message = "数据正在被其他请求更新，请重试"
        if request.path.startswith("/api/"):
            return jsonify({"error": message}), 409
        return message, 409
    app.logger.error("Database operation failed", exc_info=exc)
    if request.path.startswith("/api/"):
        return jsonify({"error": "数据库暂时不可用"}), 503
    return "数据库暂时不可用", 503


MANAGEMENT_ENDPOINTS = {
    "add_player",
    "edit_player",
    "delete_player",
    "teams",
    "my_team",
    "team_detail",
    "add_team",
    "edit_team",
    "delete_team",
    "health",
    "compare_players",
    "free_agency",
    "trade_machine",
    "trade_proposals_page",
    "create_trade_proposal",
    "accept_trade_proposal",
    "reject_trade_proposal",
    "cancel_trade_proposal",
    "games",
    "game_detail",
    "play_by_play",
    "free_agents",
    "team_players",
    "validate_trade_api",
    "execute_trade_api",
}


def _is_scorekeeper_endpoint(endpoint):
    return endpoint.startswith("scorekeeper_")


def _safe_next_url():
    target = request.args.get("next") or request.form.get("next")
    if (
        target
        and target.startswith("/")
        and not target.startswith("//")
        and "\\" not in target
        and not any(ord(char) < 32 for char in target)
    ):
        return target
    return None


@app.before_request
def enforce_access():
    endpoint = request.endpoint
    if endpoint is None or endpoint == "static":
        if endpoint is None and session.get("user_id"):
            # 让 404 等未匹配路由也能带着登录身份渲染导航与页脚
            g.user = get_user_by_id(session["user_id"])
        return None
    unsafe_method = request.method not in {"GET", "HEAD", "OPTIONS"}
    if endpoint in {"login", "logout"}:
        if unsafe_method:
            try:
                require_csrf()
            except Forbidden:
                if endpoint != "login":
                    raise
                if not session.get("user_id"):
                    flash("登录页面已失效，请重新选择身份并登录。", "warning")
                return redirect(url_for("login", next=_safe_next_url()), code=303)
        return None

    user_id = session.get("user_id")
    if not user_id:
        if request.path.startswith("/api/"):
            return jsonify({"error": "未登录或会话已过期"}), 401
        return redirect(url_for("login", next=request.full_path.rstrip("?")))

    user = get_user_by_id(user_id)
    if user is None or user["role"] not in ROLE_LABELS:
        session.clear()
        if request.path.startswith("/api/"):
            return jsonify({"error": "账号不存在或已被移除"}), 401
        return redirect(url_for("login"))

    g.user = user
    if unsafe_method:
        require_csrf()

    if user["role"] == ROLE_SCOREKEEPER and (endpoint in MANAGEMENT_ENDPOINTS or request.blueprint == "free_agency_api"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "记分员无权限访问管理层功能"}), 403
        flash("记分员账号没有管理层功能权限", "warning")
        return redirect(url_for("index"))

    if user["role"] != ROLE_SCOREKEEPER and _is_scorekeeper_endpoint(endpoint):
        if request.path.startswith("/api/"):
            return jsonify({"error": "管理层账号没有计分员权限"}), 403
        flash("管理层账号没有计分员权限", "warning")
        return redirect(url_for("index"))

    return None


@app.context_processor
def inject_auth_context():
    return {
        "current_user": getattr(g, "user", None),
        "ROLE_MANAGEMENT": ROLE_MANAGEMENT,
        "ROLE_LEAGUE_ADMIN": ROLE_LEAGUE_ADMIN,
        "can_edit_team": can_edit_team,
        "csrf_token": csrf_token,
        "ROLE_SCOREKEEPER": ROLE_SCOREKEEPER,
        "ROLE_LABELS": ROLE_LABELS,
    }


def pagination_args(default_per_page=20):
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", default_per_page, type=int)))
    return page, per_page


def search_term():
    return request.args.get("q", "").strip()


def current_season():
    season = request.args.get("season", SEASONS[-1])
    return season if season in SEASONS else SEASONS[-1]


def get_teams():
    return fetch_all(
        """
        SELECT id, city, name, abbreviation
        FROM teams WHERE archived_at IS NULL
        ORDER BY name
        """
    )


def get_team_or_404(team_id):
    team = fetch_one("SELECT * FROM teams WHERE id = %s", (team_id,))
    if team is None:
        abort(404)
    return team


def team_detail_data(team_id, season):
    year = int(season.split("-", 1)[0])
    team = get_team_or_404(team_id)
    coach = fetch_one(
        "SELECT * FROM coaches WHERE team_id = %s",
        (team_id,),
    )
    cap = fetch_one("SELECT * FROM salary_caps WHERE season = %s", (season,))
    standings = next(
        (row for row in team_standings(season) if row["id"] == team_id),
        None,
    )
    roster = fetch_all(
        f"""
        SELECT
            p.id,
            p.name,
            p.position,
            p.jersey_number,
            p.age,
            p.salary,
            {season_salary_sql()} AS contract_salary,
            c.start_year,
            c.end_year,
            ROUND(AVG(CASE WHEN g.id IS NOT NULL THEN s.points END), 1) AS ppg,
            ROUND(AVG(CASE WHEN g.id IS NOT NULL THEN s.rebounds END), 1) AS rpg,
            ROUND(AVG(CASE WHEN g.id IS NOT NULL THEN s.assists END), 1) AS apg
        FROM players p
        LEFT JOIN contracts c ON c.active_player_id = p.id
            AND c.team_id = p.team_id AND c.start_year <= %s AND c.end_year >= %s
        LEFT JOIN player_game_stats s ON s.player_id = p.id AND s.team_id = p.team_id
        LEFT JOIN games g
            ON g.id = s.game_id AND g.season = %s AND g.status = 'finished'
        WHERE p.team_id = %s
        GROUP BY
            p.id, p.name, p.position, p.jersey_number, p.age, p.salary,
            c.annual_salary, c.annual_increase_pct, c.start_year, c.end_year
        ORDER BY p.jersey_number
        """,
        (year, year, year, season, team_id),
    )
    payroll = team_payroll(team_id, season)
    cap_amount = cap["cap_amount"] if cap else 0
    cap_space = cap_amount - payroll
    return team, coach, cap, standings, roster, payroll, cap_space


def _decode_history_player_ids(raw_value):
    try:
        values = json.loads(raw_value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(values, list):
        return []
    player_ids = []
    for value in values:
        try:
            player_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return player_ids


def team_transaction_history(team_id):
    signings = fetch_all(
        """
        SELECT id, signed_at, player_id, player_name, season,
               contract_years, annual_salary, jersey_number
        FROM free_agent_signing_history
        WHERE team_id = %s
        ORDER BY signed_at DESC, id DESC
        LIMIT 50
        """,
        (team_id,),
    )
    trade_rows = fetch_all(
        """
        SELECT th.id, th.trade_date, th.team1_id, th.team2_id,
               th.team1_outgoing, th.team2_outgoing,
               t1.city AS team1_city, t1.name AS team1_name,
               t1.abbreviation AS team1_abbreviation,
               t2.city AS team2_city, t2.name AS team2_name,
               t2.abbreviation AS team2_abbreviation
        FROM trade_history th
        JOIN teams t1 ON t1.id = th.team1_id
        JOIN teams t2 ON t2.id = th.team2_id
        WHERE th.team1_id = %s OR th.team2_id = %s
        ORDER BY th.trade_date DESC, th.id DESC
        LIMIT 50
        """,
        (team_id, team_id),
    )

    decoded_trades = []
    all_player_ids = set()
    for row in trade_rows:
        team1_outgoing = _decode_history_player_ids(row["team1_outgoing"])
        team2_outgoing = _decode_history_player_ids(row["team2_outgoing"])
        all_player_ids.update(team1_outgoing)
        all_player_ids.update(team2_outgoing)
        decoded_trades.append((row, team1_outgoing, team2_outgoing))

    player_map = {}
    if all_player_ids:
        placeholders = ",".join(["%s"] * len(all_player_ids))
        players = fetch_all(
            f"SELECT id, name FROM players WHERE id IN ({placeholders})",
            tuple(sorted(all_player_ids)),
        )
        player_map = {int(player["id"]): player["name"] for player in players}

    trades = []
    for row, team1_outgoing, team2_outgoing in decoded_trades:
        is_team1 = int(row["team1_id"]) == int(team_id)
        sent_ids = team1_outgoing if is_team1 else team2_outgoing
        received_ids = team2_outgoing if is_team1 else team1_outgoing
        opponent_prefix = "team2" if is_team1 else "team1"
        trades.append(
            {
                "id": row["id"],
                "trade_date": row["trade_date"],
                "opponent_name": f"{row[f'{opponent_prefix}_city']} {row[f'{opponent_prefix}_name']}",
                "opponent_abbreviation": row[f"{opponent_prefix}_abbreviation"],
                "sent_players": [
                    player_map.get(player_id, f"球员 #{player_id}")
                    for player_id in sent_ids
                ],
                "received_players": [
                    player_map.get(player_id, f"球员 #{player_id}")
                    for player_id in received_ids
                ],
            }
        )
    return signings, trades


def trade_proposals_for_team(team_id):
    rows = fetch_all(
        """
        SELECT tp.*, pt.city AS proposer_city, pt.name AS proposer_name,
               pt.abbreviation AS proposer_abbreviation,
               rt.city AS receiver_city, rt.name AS receiver_name,
               rt.abbreviation AS receiver_abbreviation
        FROM trade_proposals tp
        JOIN teams pt ON pt.id = tp.proposer_team_id
        JOIN teams rt ON rt.id = tp.receiver_team_id
        WHERE tp.proposer_team_id = %s OR tp.receiver_team_id = %s
        ORDER BY (tp.status = 'pending') DESC, tp.created_at DESC, tp.id DESC
        """,
        (team_id, team_id),
    )
    all_player_ids = set()
    for row in rows:
        row["proposer_player_ids"], followup = decode_proposal_side(row.pop("proposer_outgoing"))
        row["receiver_player_ids"], _ = decode_proposal_side(row.pop("receiver_outgoing"))
        row["is_sender"] = int(row["proposer_team_id"]) == int(team_id)
        row["followup"] = followup if row["is_sender"] else None
        all_player_ids.update(row["proposer_player_ids"])
        all_player_ids.update(row["receiver_player_ids"])
        if row["followup"]:
            all_player_ids.add(followup["player_id"])

    player_map = {}
    if all_player_ids:
        placeholders = ",".join(["%s"] * len(all_player_ids))
        players = fetch_all(
            f"SELECT id, name FROM players WHERE id IN ({placeholders})",
            tuple(sorted(all_player_ids)),
        )
        player_map = {int(player["id"]): player["name"] for player in players}

    for row in rows:
        row["proposer_players"] = [
            player_map.get(player_id, f"球员 #{player_id}")
            for player_id in row["proposer_player_ids"]
        ]
        row["receiver_players"] = [
            player_map.get(player_id, f"球员 #{player_id}")
            for player_id in row["receiver_player_ids"]
        ]
        if row["followup"]:
            player_id = row["followup"]["player_id"]
            row["followup"]["name"] = player_map.get(player_id, f"球员 #{player_id}")
    return rows


def get_player_or_404(player_id):
    player = fetch_one(
        """
        SELECT p.*,
               COALESCE(t.name, 'FREE') AS team_name,
               COALESCE(t.abbreviation, 'FA') AS abbreviation
        FROM players p
        LEFT JOIN teams t ON t.id = p.team_id
        WHERE p.id = %s
        """,
        (player_id,),
    )
    if player is None:
        abort(404)
    return player


def get_game_or_404(game_id):
    game = fetch_one(
        """
        SELECT g.*,
               ht.name AS home_team_name,
               ht.abbreviation AS home_team_abbr,
               at.name AS away_team_name,
               at.abbreviation AS away_team_abbr
        FROM games g
        JOIN teams ht ON ht.id = g.home_team_id
        JOIN teams at ON at.id = g.away_team_id
        WHERE g.id = %s
        """,
        (game_id,),
    )
    if game is None:
        abort(404)
    return game


@ttl_cache(seconds=60)
def team_standings(season):
    return fetch_all(
        """
        SELECT
            t.id,
            t.city,
            t.name,
            t.abbreviation,
            COUNT(g.id) AS games_played,
            COALESCE(SUM(CASE
                WHEN (g.home_team_id = t.id AND g.home_score > g.away_score)
                  OR (g.away_team_id = t.id AND g.away_score > g.home_score)
                THEN 1 ELSE 0
            END), 0) AS wins,
            COALESCE(SUM(CASE WHEN g.home_team_id = t.id THEN g.home_score ELSE g.away_score END), 0) AS points_for,
            COALESCE(SUM(CASE WHEN g.home_team_id = t.id THEN g.away_score ELSE g.home_score END), 0) AS points_against
        FROM teams t
        LEFT JOIN games g
            ON (g.home_team_id = t.id OR g.away_team_id = t.id)
           AND g.status = 'finished'
           AND g.season = %s
        GROUP BY t.id, t.city, t.name, t.abbreviation
        ORDER BY wins DESC, (points_for - points_against) DESC
        """,
        (season,),
    )


@ttl_cache(seconds=60)
def scoring_leaders(season):
    return fetch_all(
        """
        SELECT
            p.id,
            p.name,
            COALESCE(t.name, 'FREE') AS team_name,
            COALESCE(t.abbreviation, 'FA') AS abbreviation,
            COUNT(s.id) AS games_played,
            SUM(s.points) AS total_points,
            ROUND(AVG(s.points), 1) AS ppg
        FROM players p
        JOIN player_game_stats s ON s.player_id = p.id
        JOIN teams t ON t.id = p.team_id
        JOIN games g ON g.id = s.game_id
        WHERE g.season = %s AND g.status = 'finished'
        GROUP BY p.id, p.name, t.name, t.abbreviation
        ORDER BY ppg DESC, total_points DESC
        LIMIT 15
        """,
        (season,),
    )


@ttl_cache(seconds=60)
def efficiency_leaders(season):
    return fetch_all(
        """
        SELECT
            p.id,
            p.name,
            t.name AS team_name,
            t.abbreviation,
            COUNT(s.id) AS games_played,
            ROUND(
                SUM(
                    s.points + s.rebounds + s.assists + s.steals + s.blocks
                    - (s.field_goals_attempted - s.field_goals_made)
                    - s.turnovers
                ) / COUNT(s.id),
                2
            ) AS efficiency
        FROM players p
        JOIN player_game_stats s ON s.player_id = p.id
        JOIN teams t ON t.id = p.team_id
        JOIN games g ON g.id = s.game_id
        WHERE g.season = %s AND g.status = 'finished'
        GROUP BY p.id, p.name, t.name, t.abbreviation
        ORDER BY efficiency DESC
        LIMIT 15
        """,
        (season,),
    )


def advanced_player_stats(season, page=1, per_page=20, search=""):
    stats_cte = """
        WITH player_stats AS (
        SELECT
            p.id,
            p.name,
            t.name AS team_name,
            t.abbreviation,
            COUNT(s.id) AS games_played,
            SUM(s.minutes_played) AS total_minutes,
            ROUND(AVG(s.minutes_played), 1) AS mpg,
            ROUND(AVG(s.points), 1) AS ppg,
            ROUND(AVG(s.rebounds), 1) AS rpg,
            ROUND(AVG(s.assists), 1) AS apg,
            ROUND(AVG(s.steals), 1) AS spg,
            ROUND(AVG(s.blocks), 1) AS bpg,
            SUM(s.points) AS total_points,
            SUM(s.plus_minus) AS plus_minus,
            ROUND(
                SUM(s.points)
                / (2 * (SUM(s.field_goals_attempted) + 0.44 * SUM(s.free_throws_attempted)))
                * 100,
                1
            ) AS ts_percent,
            ROUND(SUM(s.plus_minus) / SUM(s.minutes_played) * 48, 1) AS net_rating_per48
        FROM players p
        JOIN player_game_stats s ON s.player_id = p.id
        JOIN teams t ON t.id = p.team_id
        JOIN games g ON g.id = s.game_id
        WHERE g.season = %s AND g.status = 'finished'
        GROUP BY p.id, p.name, t.name, t.abbreviation
        )
    """
    search_clause = ""
    search_params = ()
    if search:
        like = f"%{search}%"
        search_clause = """
            WHERE ps.name LIKE %s
               OR ps.team_name LIKE %s
               OR ps.abbreviation LIKE %s
               OR CAST(ps.games_played AS CHAR) LIKE %s
               OR CAST(ps.mpg AS CHAR) LIKE %s
               OR CAST(ps.ppg AS CHAR) LIKE %s
               OR CAST(ps.rpg AS CHAR) LIKE %s
               OR CAST(ps.apg AS CHAR) LIKE %s
               OR CAST(ps.spg AS CHAR) LIKE %s
               OR CAST(ps.bpg AS CHAR) LIKE %s
               OR CAST(ps.plus_minus AS CHAR) LIKE %s
               OR CAST(ps.net_rating_per48 AS CHAR) LIKE %s
               OR CAST(ps.ts_percent AS CHAR) LIKE %s
        """
        search_params = (like,) * 13

    params = (season, *search_params)
    total = fetch_one(
        f"""
        {stats_cte}
        SELECT COUNT(*) AS total
        FROM player_stats ps
        {search_clause}
        """,
        params,
    )["total"]
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    rows = fetch_all(
        f"""
        {stats_cte}
        SELECT ps.*
        FROM player_stats ps
        {search_clause}
        ORDER BY ps.ts_percent DESC, ps.ppg DESC, ps.name
        LIMIT %s OFFSET %s
        """,
        (*params, per_page, (page - 1) * per_page),
    )
    return rows, total, page, total_pages


@ttl_cache(seconds=60)
def advanced_team_stats(season):
    rows = fetch_all(
        """
        WITH team_totals AS (
            SELECT
                p.team_id,
                SUM(s.field_goals_attempted) AS fga,
                SUM(s.free_throws_attempted) AS fta,
                SUM(s.turnovers) AS turnovers
            FROM player_game_stats s
            JOIN players p ON p.id = s.player_id
            JOIN games g ON g.id = s.game_id
            WHERE g.season = %s AND g.status = 'finished'
            GROUP BY p.team_id
        )
        SELECT
            t.id,
            t.name,
            t.abbreviation,
            tt.fga + (0.44 * tt.fta) + tt.turnovers AS possessions,
            SUM(CASE WHEN g.home_team_id = t.id THEN g.home_score ELSE g.away_score END) AS points_for,
            SUM(CASE WHEN g.home_team_id = t.id THEN g.away_score ELSE g.home_score END) AS points_against,
            ROUND(
                SUM(CASE WHEN g.home_team_id = t.id THEN g.home_score ELSE g.away_score END)
                / (tt.fga + 0.44 * tt.fta + tt.turnovers) * 100,
                1
            ) AS off_rating,
            ROUND(
                SUM(CASE WHEN g.home_team_id = t.id THEN g.away_score ELSE g.home_score END)
                / (tt.fga + 0.44 * tt.fta + tt.turnovers) * 100,
                1
            ) AS def_rating
        FROM teams t
        JOIN team_totals tt ON tt.team_id = t.id
        JOIN games g
            ON (g.home_team_id = t.id OR g.away_team_id = t.id)
           AND g.status = 'finished'
        WHERE g.season = %s
        GROUP BY t.id, t.name, t.abbreviation, tt.fga, tt.fta, tt.turnovers
        ORDER BY off_rating - def_rating DESC
        """,
        (season, season),
    )
    for row in rows:
        row["net_rating"] = round(row["off_rating"] - row["def_rating"], 1)
    return rows


@ttl_cache(seconds=300)
def league_shot_zone_stats(season):
    return fetch_all(
        """
        SELECT sa.zone, COUNT(*) AS attempts, SUM(sa.made) AS made
        FROM shot_attempts sa
        JOIN games g ON g.id = sa.game_id
        WHERE g.season = %s AND g.status = 'finished' AND sa.voided_at IS NULL
        GROUP BY sa.zone
        """,
        (season,),
    )


def shot_zone_stats(player_id, season, game_ids=None):
    league_rows = league_shot_zone_stats(season)
    date_filter = ""
    if game_ids is not None:
        date_filter = " AND g.id IN (" + ",".join(["%s"] * len(game_ids)) + ")" if game_ids else " AND 1=0"
    player_rows = fetch_all(
        f"""
        SELECT sa.zone, COUNT(*) AS attempts, SUM(sa.made) AS made
        FROM shot_attempts sa
        JOIN games g ON g.id = sa.game_id
        WHERE sa.player_id = %s AND g.season = %s AND g.status = 'finished' AND sa.voided_at IS NULL
          {date_filter}
        GROUP BY sa.zone
        """,
        (player_id, season, *(game_ids or [])),
    )

    league = {row["zone"]: row for row in league_rows}
    player = {row["zone"]: row for row in player_rows}
    zones = []

    for zone in SHOT_ZONES:
        league_attempts = int(league.get(zone, {}).get("attempts") or 0)
        league_made = int(league.get(zone, {}).get("made") or 0)
        player_attempts = int(player.get(zone, {}).get("attempts") or 0)
        player_made = int(player.get(zone, {}).get("made") or 0)

        league_pct = league_made / league_attempts if league_attempts else 0
        player_pct = player_made / player_attempts if player_attempts else 0
        diff = player_pct - league_pct

        zones.append(
            {
                "zone": zone,
                "attempts": player_attempts,
                "made": player_made,
                "player_pct": round(player_pct * 100, 1),
                "league_attempts": league_attempts,
                "league_pct": round(league_pct * 100, 1),
                "diff": round(diff * 100, 1),
                "path": SHOT_ZONE_GEOMETRY[zone]["path"],
                "text_x": SHOT_ZONE_GEOMETRY[zone]["text_x"],
                "text_y": SHOT_ZONE_GEOMETRY[zone]["text_y"],
                "text_dy": SHOT_ZONE_GEOMETRY[zone]["text_dy"],
            }
        )

    return zones


def lineup_name_map():
    return {
        row["id"]: row["name"]
        for row in fetch_all("SELECT id, name FROM players")
    }


def attach_lineup_names(rows, name_map):
    for row in rows:
        ids = [int(value) for value in row["player_ids"].split(",")]
        row["player_names"] = ", ".join(name_map.get(value, str(value)) for value in ids)
    return rows


def lineup_page(page, per_page, search=""):
    name_map = lineup_name_map()
    where_clause = ""
    search_params = ()
    if search:
        like = f"%{search}%"
        where_clause = """
            WHERE t.name LIKE %s
               OR t.abbreviation LIKE %s
               OR lc.player_ids LIKE %s
        """
        search_params = (like, like, like)

    total = fetch_one(
        f"""
        SELECT COUNT(*) AS total
        FROM lineup_combinations lc
        JOIN teams t ON t.id = lc.team_id
        {where_clause}
        """,
        search_params,
    )["total"]
    rows = fetch_all(
        f"""
        SELECT lc.*, t.name AS team_name, t.abbreviation
        FROM lineup_combinations lc
        JOIN teams t ON t.id = lc.team_id
        {where_clause}
        ORDER BY lc.combination_size, lc.net_rating DESC
        LIMIT %s OFFSET %s
        """,
        (*search_params, per_page, (page - 1) * per_page),
    )
    return attach_lineup_names(rows, name_map), total


def lineup_highlights():
    name_map = lineup_name_map()
    highlights = {}

    for key, size, order in (
        ("best_five", 5, "DESC"),
        ("worst_five", 5, "ASC"),
        ("best_three", 3, "DESC"),
        ("best_two", 2, "DESC"),
    ):
        row = fetch_one(
            """
            SELECT lc.*, t.name AS team_name, t.abbreviation
            FROM lineup_combinations lc
            JOIN teams t ON t.id = lc.team_id
            WHERE lc.combination_size = %s
            ORDER BY lc.net_rating {order}
            LIMIT 1
            """.replace("{order}", order),
            (size,),
        )
        if row:
            attach_lineup_names([row], name_map)
            highlights[key] = row[0] if isinstance(row, list) else row
        else:
            highlights[key] = None

    return highlights


def health_data():
    return fetch_all(
        """
        SELECT
            h.*,
            p.name AS player_name,
            p.position,
            t.name AS team_name,
            t.abbreviation
        FROM player_health h
        JOIN players p ON p.id = h.player_id
        JOIN teams t ON t.id = p.team_id
        ORDER BY h.fatigue_score DESC, h.injury_status
        """
    )


def _role_home(role):
    return {ROLE_LEAGUE_ADMIN: "league_dashboard", ROLE_SCOREKEEPER: "scorekeeper_calendar"}.get(role, "index")


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for(_role_home(session.get("role"))))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        selected_role = request.form.get("role")
        user = authenticate_user(username, password)

        if user is None:
            flash("账号或密码错误", "danger")
        elif selected_role not in ROLE_LABELS:
            flash("请选择正确的登录身份", "danger")
        elif user["role"] != selected_role:
            flash("所选登录身份与账号角色不匹配", "danger")
        else:
            session.clear()
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            next_url = _safe_next_url()
            if next_url:
                return redirect(next_url)
            return redirect(url_for(_role_home(user["role"])))

    return render_template("login.html", next_url=request.args.get("next", ""), season=current_season()), {
        "Cache-Control": "no-store",
    }


@app.post("/logout")
def logout():
    session.clear()
    flash("已退出登录", "info")
    return redirect(url_for("login"))


@ttl_cache(seconds=120)
def league_recent_games(season, limit=8):
    return fetch_all(
        """
        SELECT g.id, g.game_date, g.status, g.home_score, g.away_score,
               ht.name AS home_team_name, ht.city AS home_team_city, ht.abbreviation AS home_team_abbr,
               at.name AS away_team_name, at.city AS away_team_city, at.abbreviation AS away_team_abbr
        FROM games g
        JOIN teams ht ON ht.id = g.home_team_id
        JOIN teams at ON at.id = g.away_team_id
        WHERE g.season = %s AND g.status IN ('finished', 'live')
        ORDER BY g.game_date DESC, g.id DESC
        LIMIT %s
        """,
        (season, limit),
    )


@ttl_cache(seconds=120)
def league_snapshot(season):
    return fetch_one(
        """
        SELECT COUNT(*) AS total_games,
               COALESCE(SUM(CASE WHEN status = 'finished' THEN 1 ELSE 0 END), 0) AS games_finished,
               AVG(CASE WHEN status = 'finished' THEN home_score + away_score END) AS avg_total,
               AVG(CASE WHEN status = 'finished' THEN home_score END) AS avg_home,
               MAX(game_date) AS latest_date
        FROM games
        WHERE season = %s
        """,
        (season,),
    )


@app.route("/")
def index():
    season = current_season()
    return render_template(
        "index.html",
        standings=team_standings(season),
        scorers=scoring_leaders(season),
        efficient_players=efficiency_leaders(season),
        recent_games=league_recent_games(season),
        snapshot=league_snapshot(season),
        season=season,
        seasons=SEASONS,
    )


@app.get("/league")
def league_dashboard():
    require_league_admin()
    season = current_season()
    return render_template("league_dashboard.html", season=season, overview=league_overview(season)), {
        "Cache-Control": "private, no-store",
    }


@app.route("/league/games/<int:game_id>", methods=["GET", "POST"])
def league_game(game_id):
    require_league_admin()
    if request.method == "POST":
        action = choice(request.form.get("action"), "比赛操作", ("finish", "open", "resolve"))
        note = request.form.get("note", "").strip()
        if action == "finish":
            if len(note) > 500:
                raise BusinessError("完赛说明最多 500 字")
            try:
                scorekeeper.finish_game(
                    game_id, user_id=g.user["id"], note=note or "后续无新增统计，按当前已录入比分完赛。",
                    check_date=False,
                )
            except scorekeeper.ScorekeeperError as exc:
                flash(exc.message, "warning")
            else:
                flash("比赛已完赛。当前比分和已录入统计已保留。", "success")
            return redirect(url_for("league_game", game_id=game_id), code=303)
        if not 1 <= len(note) <= 500:
            raise BusinessError("请填写 1 至 500 字的核查说明")
        update_game_review(game_id, g.user["id"], action, note)
        flash("完赛核查记录已更新", "success")
        return redirect(url_for("league_game", game_id=game_id), code=303)
    game, starters, review = game_operations(game_id)
    return render_template("league_game.html", game=game, starters=starters, review=review), {
        "Cache-Control": "private, no-store",
    }


@app.route("/league/draft-pool", methods=["GET", "POST"])
def league_draft_pool():
    require_league_admin()
    if request.method == "POST":
        form = request.form
        year = bounded_int(form.get("draft_year"), "选秀年份", 2000, 2100)
        position = choice(form.get("position"), "位置", POSITIONS)
        name = form.get("name", "").strip()
        if not 1 <= len(name) <= 80:
            raise BusinessError("请填写候选人姓名")
        source, observation = parse_profile_fields(form)
        games = bounded_int(form.get("games"), "样本场次", 1, 999)
        minutes = bounded_int(form.get("minutes"), "场均分钟", 1, 60)
        metrics = [bounded_float(form.get(key), key, 0, 99) for key in ("assists", "turnovers", "rebounds", "threes")]
        try:
            execute(
                """
                INSERT INTO draft_prospects
                    (draft_year, name, position, source, games, minutes, assists, turnovers, rebounds, threes, observation)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (year, name, position, source, games, minutes, *metrics, observation),
            )
        except pymysql.err.IntegrityError as exc:
            if exc.args[0] != 1062:
                raise
            raise BusinessError("同届已有同名候选人", 409) from exc
        flash("候选人已发布到各队的公共候选池", "success")
        return redirect(url_for("league_draft_pool"), code=303)
    candidates = fetch_all("SELECT draft_year, name, position, source, observation FROM draft_prospects ORDER BY draft_year DESC, name")
    for row in candidates:
        row["profile"] = external_profile(row)
    return render_template(
        "league_draft_pool.html", candidates=candidates, positions=POSITIONS,
        levels=EXTERNAL_LEVELS, profile_fields=PROFILE_FIELDS,
    )


@app.route("/analytics")
def analytics():
    season = current_season()
    metric = request.args.get("metric", "points")
    if metric not in LEADER_METRICS:
        metric = "points"
    mode = "total" if request.args.get("mode") == "total" else "average"
    tab = "all" if request.args.get("tab") == "all" else "ranking"
    page, per_page = pagination_args()
    data = directory_data(season, metric, mode, tab, search_term(), page, per_page)
    return render_template(
        "analytics.html", season=season,seasons=SEASONS,metric=metric,mode=mode,tab=tab,
        metrics=LEADER_METRICS,boards=data["boards"],players=data["rows"],total=data["total"],
        page=data["page"],per_page=per_page,total_pages=data["total_pages"],offset=data["offset"],
        search=search_term(),
    )


@app.route("/shot-chart")
def shot_chart():
    season = current_season()
    player_rows = fetch_all(
        """
        SELECT p.id, p.name, COALESCE(t.name, '自由球员') AS team_name,
               COALESCE(t.abbreviation, 'FA') AS abbreviation
        FROM players p
        LEFT JOIN teams t ON t.id = p.team_id
        ORDER BY t.id IS NULL, t.name, p.jersey_number, p.id
        """
    )
    player_id = request.args.get("player_id", type=int)
    if "player_id" not in request.args and player_rows:
        player_id = player_rows[0]["id"]
    selected = next((row for row in player_rows if row["id"] == player_id), None)
    mode = "efficiency" if request.args.get("mode") == "efficiency" else "volume"
    scope = "recent" if request.args.get("scope") == "recent" else "season"
    outcome = request.args.get("outcome", "all")
    if outcome not in {"all", "made", "missed"}:
        outcome = "all"
    page = max(1, request.args.get("shot_page", 1, type=int))
    game_ids = recent_shooting_games(player_id, season) if selected and scope == "recent" else None
    zones = shot_zone_stats(player_id, season, game_ids) if selected else []
    total = annotate_zones(zones)
    for zone in zones:
        zone["label"] = ZONE_LABELS[zone["zone"]]
    focus = next((row for row in zones if row["zone"] == request.args.get("zone")), None)
    most_used = max(zones, key=lambda row: row["attempts"]) if total else None
    focus = focus or most_used
    rows, detail_count, page = (
        zone_shots(player_id, season, focus["zone"], game_ids, outcome, page)
        if focus and focus["attempts"] else ([], 0, 1)
    )
    return render_template(
        "shot_chart.html",
        players=player_rows,
        selected=selected,
        zones=zones,
        season=season,
        seasons=SEASONS,
        mode=mode, scope=scope, recent_count=len(game_ids) if game_ids is not None else None, outcome=outcome,
        total=total, focus=focus, shots=rows, detail_count=detail_count, page=page, detail_page_size=DETAIL_PAGE_SIZE,
        selection_missing=not selected and "player_id" in request.args,
    )


def _package_serializer():
    return URLSafeTimedSerializer(app.secret_key, salt="team-roster-package")


@app.route("/salary", methods=["GET", "POST"])
def salary():
    team_id = planning_team_id()
    season = current_season()
    team, years, contracts = team_planning.salary_plan(team_id, season)
    recommendations = None
    if request.method == "POST":
        recommendations = recommend_packages(team_id, season)
        serializer = _package_serializer()
        for plan in recommendations["plans"]:
            plan["token"] = serializer.dumps(plan.pop("payload"))
    return render_template(
        "salary.html",
        team=team,
        season=season,
        years=years,
        contracts=contracts,
        recommendations=recommendations,
        readonly=False,
    ), {"Cache-Control": "private, no-store"}


@app.post("/salary/packages/apply")
def apply_roster_package():
    team_id = management_team_id()
    try:
        package = _package_serializer().loads(request.form.get("token", ""), max_age=900)
    except BadData:
        raise BusinessError("方案已失效，请重新生成", 409)
    if package["trade"]["team1_id"] != team_id:
        abort(403)
    trade_payload = _trade_payload(package["trade"], team_id)
    followup = package["followup"]
    result, status = _submit_trade(trade_payload, followup=followup)
    if status >= 400:
        flash(result.get("error") or "；".join(result["reasons"]), "danger")
        return redirect(url_for("salary", season=trade_payload["season"]), code=303)
    flash("组合方案已关联交易申请。对方接受后可在发出的交易中确认签约。", "success")
    return redirect(url_for("trade_proposals_page"), code=303)


@app.post("/api/salary/preview/<kind>")
def salary_preview(kind):
    team_id = management_team_id()
    payload = json_object()
    if kind == "signing":
        result = team_planning.signing_preview(team_id, payload)
    elif kind == "trade":
        result = team_planning.trade_preview(team_id, _trade_payload(payload, team_id))
    else:
        abort(404)
    return jsonify(result), {"Cache-Control": "private, no-store"}


@app.route("/health")
def health():
    return render_template("health.html", rows=health_data())


@app.route("/scouting", methods=["GET", "POST"])
def scouting():
    team_id = planning_team_id()
    season = current_season()
    values = request.form if request.method == "POST" else request.args
    raw_year = values.get("year")
    year = bounded_int(raw_year, "选秀年份", 2000, 2100) if raw_year else None
    comparison = None
    recommendations = None
    if request.method == "POST":
        action = choice(values.get("action"), "操作", ("save", "remove", "compare", "recommend"))
        if action in {"save", "remove"}:
            prospect_id = bounded_int(values.get("prospect_id"), "候选人")
            if action == "save":
                priority = bounded_int(values.get("priority") or 999, "优先顺序", 1, 999)
                role = values.get("role_note", "").strip()
                notes = values.get("notes", "").strip()
                if len(role) > 120 or len(notes) > 600:
                    raise BusinessError("预期角色或观察笔记过长")
                team_planning.update_draft_board(team_id, year, prospect_id, priority, role, notes)
            else:
                team_planning.update_draft_board(team_id, year, prospect_id)
            flash("本队选秀板已更新", "success")
            return redirect(url_for("scouting", year=year, season=season), code=303)
        if action == "compare":
            raw_ids = values.getlist("candidate")
            if not 2 <= len(raw_ids) <= 3:
                raise BusinessError("请选择 2 至 3 名候选人")
            selected = {bounded_int(value, "候选人") for value in raw_ids}
    workspace = team_planning.draft_workspace(team_id, season, year)
    if request.method == "POST":
        if action == "recommend":
            recommendations = team_planning.draft_recommendations(workspace)
        else:
            comparison = team_planning.draft_comparison(workspace, selected)
    return render_template(
        "scouting.html",
        workspace=workspace,
        comparison=comparison,
        recommendations=recommendations,
        season=season,
        current_team_id=team_id,
        readonly=False,
    ), {
        "Cache-Control": "private, no-store",
    }


@app.get("/compare")
def compare_players():
    season = current_season()
    mode = "per36" if request.args.get("mode") == "per36" else "game"
    players = fetch_all(
        """
        SELECT p.id, p.name, p.age, p.position, p.team_id,
               COALESCE(t.name, '自由球员') AS team_name, COALESCE(t.abbreviation, 'FA') AS abbreviation
        FROM players p LEFT JOIN teams t ON t.id = p.team_id
        ORDER BY t.id IS NULL, t.name, p.jersey_number, p.id
        """
    )
    choices = {player["id"]: player for player in players}
    player1_id = request.args.get("player1_id", type=int)
    player2_id = request.args.get("player2_id", type=int)
    error = None

    if "player1_id" not in request.args and players:
        player1_id = players[0]["id"]
    player1 = choices.get(player1_id)
    if "player2_id" not in request.args and player1:
        other_players = [row for row in players if row["id"] != player1_id]
        preferred = next((row for row in other_players if row["position"] == player1["position"]), None)
        player2_id = (preferred or other_players[0])["id"] if other_players else None
    player2 = choices.get(player2_id)
    if any(f"player{index}_id" in request.args and selected is None
           for index, selected in enumerate((player1, player2), 1)):
        error = "该球员不存在，请重新选择。"
    elif player1 and player2 and player1["id"] == player2["id"]:
        error = "请选择两名不同的球员。"

    sides, box_scores, cues, zones, comparable = [], [], [], [], False
    if player1 and player2 and not error:
        for side, player in zip(("A", "B"), (player1, player2)):
            summary, recent = player_comparison_sample(player["id"], season)
            shot_rows = shot_zone_stats(player["id"], season)
            sides.append({"side": side, "player": player, "summary": summary, "recent": recent,
                          "attempts": sum(row["attempts"] for row in shot_rows), "shots": shot_rows})
        box_scores, cues, comparable = compare_box_scores(sides[0]["summary"], sides[1]["summary"], mode)
        for first, second in zip(sides[0]["shots"], sides[1]["shots"]):
            zones.append({
                "label": ZONE_LABELS[first["zone"]], "first": first, "second": second,
                "difference": round(first["player_pct"] - second["player_pct"], 1)
                if min(first["attempts"], second["attempts"]) >= 10 else None,
            })

    return render_template(
        "compare.html", players=players, player1=player1, player2=player2, mode=mode, season=season, seasons=SEASONS,
        error=error, sides=sides, box_scores=box_scores, cues=cues, zones=zones, comparable=comparable,
    )


@app.route("/free-agency")
def free_agency():
    season = current_season()
    active_team_id = management_team_id()
    teams = [get_team_or_404(active_team_id)]

    cap = fetch_one("SELECT * FROM salary_caps WHERE season = %s", (season,))
    payroll = team_payroll(active_team_id, season)

    players = fetch_all(
        """
        SELECT p.id, p.name, p.position,
               COALESCE(t.name, 'FREE') AS team_name,
               COALESCE(t.abbreviation, 'FA') AS abbreviation
        FROM players p
        LEFT JOIN teams t ON t.id = p.team_id
        WHERE p.team_id IS NULL AND p.archived_at IS NULL
        ORDER BY p.name
        """
    )
    return render_template(
        "free_agency.html",
        teams=teams,
        players=players,
        cap=cap,
        payroll=payroll,
        active_team_id=active_team_id,
        season=season,
    )


@app.route("/api/free-agents")
def free_agents():
    position = request.args.get("position")
    age = request.args.get("age", type=int)
    sql = "SELECT p.* FROM players p WHERE p.team_id IS NULL AND p.archived_at IS NULL"
    params = []
    if position:
        sql += " AND p.position = %s"
        params.append(position)
    if age:
        sql += " AND p.age <= %s"
        params.append(age)
    sql += " ORDER BY p.name"
    return jsonify(fetch_all(sql, params))


@app.route("/api/teams/<int:team_id>/players")
def team_players(team_id):
    year = int(current_season().split("-", 1)[0])
    rows = fetch_all(
        f"""
        SELECT p.id, p.name, p.position, COALESCE({season_salary_sql()} + c.incentives, 0) AS salary
        FROM players p
        LEFT JOIN contracts c ON c.active_player_id = p.id
            AND c.team_id = p.team_id AND c.start_year <= %s AND c.end_year >= %s
        WHERE p.team_id = %s AND p.archived_at IS NULL
          AND EXISTS (SELECT 1 FROM teams WHERE id = p.team_id AND archived_at IS NULL)
        ORDER BY p.position, p.jersey_number
        """,
        (year, year, year, team_id),
    )
    return jsonify(rows)


@app.route("/api/trades/validate", methods=["POST"])
def validate_trade_api():
    return jsonify(validate_trade(_trade_payload(json_object(), management_team_id())))


@app.post("/api/trades/analysis")
def trade_analysis():
    team_id = management_team_id()
    payload = _trade_payload(json_object(), team_id)
    return jsonify(analyze_trade(payload)), 200, {"Cache-Control": "no-store"}


@app.post("/api/trades/recommendations")
def suggest_trades():
    team_id = management_team_id()
    season = season_value(json_object().get("season", current_season()))
    return jsonify(trade_recommendations(team_id, season)), 200, {"Cache-Control": "no-store"}


@app.route("/api/trades/execute", methods=["POST"])
def execute_trade_api():
    return jsonify({"error": "交易必须由接收球队管理层接受后才能执行"}), 403


@app.route("/api/trade-proposals", methods=["POST"])
def create_trade_proposal():
    proposer_team_id = management_team_id()
    trade_payload = _trade_payload(json_object(), proposer_team_id)
    result, status = _submit_trade(trade_payload)
    return jsonify(result), status


def _submit_trade(trade_payload, followup=None):
    proposer_team_id = trade_payload["team1_id"]
    receiver_team_id = trade_payload["team2_id"]
    season = trade_payload["season"]
    sent = sorted(trade_payload["team1_outgoing_player_ids"])
    received = sorted(trade_payload["team2_outgoing_player_ids"])
    with transaction() as connection, connection.cursor() as cursor:
        lock_teams(cursor, proposer_team_id, receiver_team_id)
        validation = validate_trade(trade_payload, cursor)
        if not validation["is_valid"]:
            return validation, 400
        cursor.execute("SELECT id FROM users WHERE role = %s AND team_id = %s", (ROLE_MANAGEMENT, receiver_team_id))
        if cursor.fetchone() is None:
            return {"error": "接收球队尚未配置管理层账号"}, 400
        cursor.execute(
            """
            SELECT id, proposer_outgoing, receiver_outgoing FROM trade_proposals
            WHERE proposer_team_id = %s AND receiver_team_id = %s AND season = %s AND status = 'pending'
            """,
            (proposer_team_id, receiver_team_id, season),
        )
        for proposal in cursor.fetchall():
            existing_sent, saved_followup = decode_proposal_side(proposal["proposer_outgoing"])
            existing_received, _ = decode_proposal_side(proposal["receiver_outgoing"])
            if sorted(existing_sent) == sent and sorted(existing_received) == received:
                if followup is not None and saved_followup != followup:
                    return {"error": "相同的交易已在等待审批，请先处理原申请再应用新的组合方案"}, 409
                return {"message": "相同的交易方案已发送，等待对方管理层处理", "proposal_id": proposal["id"]}, 200
        if followup:
            try:
                check_followup_after_trade(cursor, trade_payload, followup)
            except BusinessError as exc:
                return {"error": str(exc)}, exc.status_code
        cursor.execute(
            """
            INSERT INTO trade_proposals
                (proposer_team_id, receiver_team_id, proposer_user_id,
                 proposer_outgoing, receiver_outgoing, season, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'pending')
            """,
            (proposer_team_id, receiver_team_id, g.user["id"], encode_proposal_side(sent, followup),
             encode_proposal_side(received), season),
        )
        proposal_id = cursor.lastrowid
    return {"message": "交易方案已发送，等待对方管理层处理", "proposal_id": proposal_id}, 201


def _trade_payload(payload, proposer_team_id):
    cleaned = {
        "team1_id": proposer_team_id,
        "team2_id": bounded_int(payload.get("team2_id"), "接收球队"),
        "season": season_value(payload.get("season", current_season())),
    }
    for field in ("team1_outgoing_player_ids", "team2_outgoing_player_ids"):
        values = payload.get(field, [])
        if not isinstance(values, list) or len(values) > 15:
            raise BusinessError("每支球队必须提交至多 15 名球员的列表")
        cleaned[field] = [bounded_int(value, "球员") for value in values]
    return cleaned


@app.route("/trade-proposals")
def trade_proposals_page():
    team_id = management_team_id()
    proposals = trade_proposals_for_team(team_id)
    return render_template(
        "trade_proposals.html",
        proposals=proposals,
        sent_proposals=[row for row in proposals if row["is_sender"]],
        received_proposals=[row for row in proposals if not row["is_sender"]],
    )


@app.route("/trade-proposals/<int:proposal_id>/accept", methods=["POST"])
def accept_trade_proposal(proposal_id):
    receiver_team_id = management_team_id()
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM trade_proposals WHERE id = %s FOR UPDATE",
                (proposal_id,),
            )
            proposal = cursor.fetchone()
            if proposal is None:
                abort(404)
            if int(proposal["receiver_team_id"]) != receiver_team_id:
                abort(403)
            if proposal["status"] != "pending":
                flash("该交易方案已经处理，不能重复操作", "warning")
                return redirect(url_for("trade_proposals_page"))

            trade_payload = {
                "team1_id": proposal["proposer_team_id"],
                "team2_id": proposal["receiver_team_id"],
                "team1_outgoing_player_ids": decode_proposal_side(proposal["proposer_outgoing"])[0],
                "team2_outgoing_player_ids": decode_proposal_side(proposal["receiver_outgoing"])[0],
                "season": proposal["season"],
            }
            result = execute_trade_with_cursor(cursor, trade_payload)
            if not result["is_valid"]:
                flash(
                    "当前阵容或薪资已变化，方案无法执行："
                    + "；".join(result["reasons"]),
                    "danger",
                )
                return redirect(url_for("trade_proposals_page"))
            cursor.execute(
                """
                UPDATE trade_proposals
                SET status = 'accepted', responded_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (proposal_id,),
            )
    invalidate_all()
    flash("交易方案已接受，球员与合同已完成交换", "success")
    return redirect(url_for("trade_proposals_page"))


@app.post("/trade-proposals/<int:proposal_id>/complete-signing")
def complete_roster_package(proposal_id):
    team_id = management_team_id()
    try:
        with transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM trade_proposals WHERE id=%s FOR UPDATE", (proposal_id,))
            proposal = cursor.fetchone()
            if proposal is None:
                abort(404)
            if proposal["proposer_team_id"] != team_id:
                abort(403)
            sent, followup = decode_proposal_side(proposal["proposer_outgoing"])
            if not followup:
                abort(404)
            if proposal["status"] != "accepted":
                raise BusinessError("交易成交后才能确认签约", 409)
            if followup.get("signed"):
                raise BusinessError("这个组合方案已完成签约", 409)
            sign_free_agent_with_cursor(cursor, team_id, followup["player_id"], followup["salary"], 1, proposal["season"])
            cursor.execute("UPDATE trade_proposals SET proposer_outgoing=%s WHERE id=%s",
                           (encode_proposal_side(sent, {**followup, "signed": True}), proposal_id))
    except BusinessError as exc:
        flash(str(exc), "danger")
    else:
        invalidate_all()
        flash("自由市场签约已完成，组合方案已应用。", "success")
    return redirect(url_for("trade_proposals_page"), code=303)


@app.route("/trade-proposals/<int:proposal_id>/reject", methods=["POST"])
def reject_trade_proposal(proposal_id):
    receiver_team_id = management_team_id()
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT receiver_team_id, status FROM trade_proposals WHERE id = %s FOR UPDATE",
                (proposal_id,),
            )
            proposal = cursor.fetchone()
            if proposal is None:
                abort(404)
            if int(proposal["receiver_team_id"]) != receiver_team_id:
                abort(403)
            if proposal["status"] != "pending":
                flash("该交易方案已经处理，不能重复操作", "warning")
                return redirect(url_for("trade_proposals_page"))
            cursor.execute(
                """
                UPDATE trade_proposals
                SET status = 'rejected', responded_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (proposal_id,),
            )
    flash("交易方案已拒绝", "success")
    return redirect(url_for("trade_proposals_page"))


@app.route("/trade-proposals/<int:proposal_id>/cancel", methods=["POST"])
def cancel_trade_proposal(proposal_id):
    proposer_team_id = management_team_id()
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT proposer_team_id, status FROM trade_proposals WHERE id = %s FOR UPDATE",
                (proposal_id,),
            )
            proposal = cursor.fetchone()
            if proposal is None:
                abort(404)
            if int(proposal["proposer_team_id"]) != proposer_team_id:
                abort(403)
            if proposal["status"] != "pending":
                flash("该交易方案已经处理，不能重复操作", "warning")
                return redirect(url_for("trade_proposals_page"))
            cursor.execute(
                """
                UPDATE trade_proposals
                SET status = 'cancelled', responded_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (proposal_id,),
            )
    flash("交易方案已撤销", "success")
    return redirect(url_for("trade_proposals_page"))


@app.route("/trade-machine")
def trade_machine():
    own_team_id = management_team_id()
    own_team = get_team_or_404(own_team_id)
    teams = [team for team in get_teams() if int(team["id"]) != own_team_id]
    return render_template(
        "trade_machine.html", own_team=own_team, teams=teams, season=current_season()
    )


@app.route("/players")
def players():
    values = request.args.to_dict()
    values["tab"] = "all"
    return redirect(url_for("analytics", **values))


@app.get("/players/<int:player_id>")
def player_detail(player_id):
    player = get_player_or_404(player_id)
    season = current_season()
    performance = player_performance(player_id, season)
    zones = [
        {**row, "label": ZONE_LABELS[row["zone"]]} for row in shot_zone_stats(player_id, season)
    ]
    shot_total = sum(row["attempts"] for row in zones)
    favorite = max(zones, key=lambda zone: zone["attempts"]) if shot_total else None
    show_contract = (
        g.user["role"] == ROLE_MANAGEMENT and player["team_id"] is not None and player["team_id"] == g.user["team_id"]
    )
    contract = player_contract(player_id, player["team_id"], season) if show_contract else None
    page, per_page = pagination_args()
    response = render_template(
        "player_detail.html", player=player, season=season, seasons=SEASONS, performance=performance,
        career_teams=player_career_teams(player_id),
        zones=zones, shot_total=shot_total, favorite=favorite, contract=contract, show_contract=show_contract,
        list_url=url_for("analytics", season=season, q=search_term() or None, page=page, per_page=per_page),
    )
    return response, {"Cache-Control": "private, no-store"}


@app.route("/players/add", methods=["GET", "POST"])
def add_player():
    require_league_admin()
    if request.method == "POST":
        try:
            data = parse_player_form(request.form)
            execute(
                """
                INSERT INTO players
                    (team_id, name, jersey_number, position, age, height_cm,
                     weight_kg, nationality, salary)
                VALUES (NULL, %(name)s, %(jersey_number)s, %(position)s,
                        %(age)s, %(height_cm)s, %(weight_kg)s, %(nationality)s, 0)
                """,
                data,
            )
            invalidate_all()
            flash("球员添加成功", "success")
            return redirect(url_for("players"))
        except ValueError as exc:
            flash(str(exc), "danger")
        except pymysql.err.IntegrityError:
            flash("球衣号码在同一支球队中已存在", "danger")

    return render_template("add.html", positions=POSITIONS)


@app.route("/players/edit/<int:player_id>", methods=["GET", "POST"])
def edit_player(player_id):
    player = get_player_or_404(player_id)

    if player["archived_at"] or not can_edit_team(player["team_id"]):
        abort(403)
    if request.method == "POST":
        try:
            data = parse_player_form(request.form)
            with transaction() as connection, connection.cursor() as cursor:
                if player["team_id"] is not None:
                    lock_teams(cursor, player["team_id"])
                locked_player = lock_player(cursor, player_id)
                if not can_edit_team(locked_player["team_id"]):
                    abort(403)
                if locked_player["team_id"] != player["team_id"]:
                    raise BusinessError("球员归属已变更，请刷新后重试", 409)
                cursor.execute(
                    """
                    UPDATE players
                    SET name = %(name)s, jersey_number = %(jersey_number)s,
                        position = %(position)s, age = %(age)s,
                        height_cm = %(height_cm)s, weight_kg = %(weight_kg)s,
                        nationality = %(nationality)s
                    WHERE id = %(player_id)s
                    """,
                    {**data, "player_id": player_id},
                )
            invalidate_all()
            flash("球员信息已更新", "success")
            return redirect(url_for("players"))
        except ValueError as exc:
            flash(str(exc), "danger")
        except pymysql.err.IntegrityError:
            flash("球衣号码在同一支球队中已存在", "danger")

    return render_template(
        "edit.html",
        player=player,
        positions=POSITIONS,
    )


@app.route("/players/delete/<int:player_id>", methods=["POST"])
@app.post("/players/<int:player_id>/archive")
def delete_player(player_id):
    require_league_admin()
    reference = get_player_or_404(player_id)
    if reference["archived_at"]:
        return redirect(url_for("players"))
    with transaction() as connection, connection.cursor() as cursor:
        if reference["team_id"] is not None:
            cursor.execute("SELECT id FROM teams WHERE id=%s FOR UPDATE", (reference["team_id"],))
            require_roster_idle(cursor, reference["team_id"])
        player = lock_player(cursor, player_id)
        if player["team_id"] != reference["team_id"]:
            raise BusinessError("球员归属已变更，请刷新后再归档", 409)
        cursor.execute("UPDATE players SET archived_at=NOW() WHERE id=%s", (player_id,))
    invalidate_all()
    flash("球员已归档，历史比赛和合同记录保留。", "success")
    return redirect(url_for("players"))


@app.route("/teams")
def teams():
    season = current_season()
    page, per_page = pagination_args()
    search = search_term()
    where_clause = ""
    search_params = ()
    if search:
        like = f"%{search}%"
        where_clause = """
            WHERE t.name LIKE %s
               OR t.city LIKE %s
               OR t.abbreviation LIKE %s
               OR c.name LIKE %s
        """
        search_params = (like, like, like, like)

    total = fetch_one(
        f"""
        SELECT COUNT(DISTINCT t.id) AS total
        FROM teams t
        LEFT JOIN coaches c ON c.team_id = t.id
        LEFT JOIN players p ON p.team_id = t.id
        {where_clause}
        """,
        search_params,
    )["total"]
    teams_data = fetch_all(
        f"""
        SELECT
            t.*,
            c.name AS coach_name,
            c.experience_years AS coach_experience,
            COUNT(p.id) AS player_count
        FROM teams t
        LEFT JOIN coaches c ON c.team_id = t.id
        LEFT JOIN players p ON p.team_id = t.id
        {where_clause}
        GROUP BY t.id, t.city, t.name, t.abbreviation, t.founded_year, t.arena, t.archived_at,
                 c.name, c.experience_years
        ORDER BY t.name
        LIMIT %s OFFSET %s
        """,
        (*search_params, per_page, (page - 1) * per_page),
    )
    total_pages = (total + per_page - 1) // per_page
    standings = {row["id"]: row for row in team_standings(season)}
    cap = fetch_one("SELECT * FROM salary_caps WHERE season = %s", (season,))
    year = int(season.split("-", 1)[0])
    payroll_rows = fetch_all(
        f"""
        SELECT team_id, COALESCE(SUM({season_salary_sql()} + incentives), 0) AS payroll
        FROM contracts c
        WHERE status = 'Active' AND start_year <= %s AND end_year >= %s
        GROUP BY team_id
        """,
        (year, year, year),
    )
    payroll_map = {row["team_id"]: int(row["payroll"]) for row in payroll_rows}
    cap_amount = cap["cap_amount"] if cap else 0
    for team in teams_data:
        record = standings.get(team["id"], {})
        team["wins"] = record.get("wins", 0)
        team["games_played"] = record.get("games_played", 0)
        team["payroll"] = payroll_map.get(team["id"], 0)
        team["cap_space"] = cap_amount - team["payroll"]
        team["cap_pct"] = min(100, round(team["payroll"] / cap_amount * 100, 1)) if cap_amount else 0
    return render_template(
        "teams.html",
        teams=teams_data,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        search=search,
    )


def team_schedule(team_id, season):
    return fetch_all(
        """
        SELECT
            g.id,
            g.game_date,
            g.home_score,
            g.away_score,
            g.status,
            g.home_team_id,
            g.away_team_id,
            ht.name AS home_team_name,
            ht.abbreviation AS home_team_abbr,
            at.name AS away_team_name,
            at.abbreviation AS away_team_abbr
        FROM games g
        JOIN teams ht ON ht.id = g.home_team_id
        JOIN teams at ON at.id = g.away_team_id
        WHERE (g.home_team_id = %s OR g.away_team_id = %s)
          AND g.season = %s
        ORDER BY g.game_date DESC, g.id DESC
        """,
        (team_id, team_id, season),
    )


@app.get("/my-team")
def my_team():
    team_id = management_team_id()
    season = current_season()
    team, coach, cap, standings, roster, payroll, cap_space = team_detail_data(team_id, season)
    matches = team_schedule(team_id, season)
    for match in matches:
        home = match["home_team_id"] == team_id
        opponent_side = "away" if home else "home"
        match.update(
            side="主场" if home else "客场",
            opponent=match[f"{opponent_side}_team_name"],
            opponent_abbr=match[f"{opponent_side}_team_abbr"],
            team_score=match["home_score"] if home else match["away_score"],
            opponent_score=match["away_score"] if home else match["home_score"],
        )
    upcoming = [match for match in reversed(matches) if match["status"] == "scheduled"][:4]
    recent = [match for match in matches if match["status"] == "finished"][:5]
    live = [match for match in matches if match["status"] == "live"]
    trend = [
        {"date": match["game_date"].strftime("%m-%d"), "scored": match["team_score"], "allowed": match["opponent_score"]}
        for match in reversed(recent)
    ]
    members = team_members(team_id, season)
    return render_template(
        "my_team.html", team=team, coach=coach, season=season, standings=standings,
        roster_count=sum(not row.get("archived_at") for row in members if row["is_current"]), cap=cap, payroll=payroll, cap_space=cap_space,
        upcoming=upcoming, recent=recent, live=live, members=members, trend=trend,
    ), {"Cache-Control": "no-store"}


@app.route("/teams/<int:team_id>")
def team_detail(team_id):
    season = current_season()
    team, coach, cap, standings, roster, payroll, cap_space = team_detail_data(team_id, season)
    signing_history, trade_history = team_transaction_history(team_id)
    return render_template(
        "team_detail.html",
        team=team,
        coach=coach,
        cap=cap,
        standings=standings,
        roster=roster,
        payroll=payroll,
        cap_space=cap_space,
        season=season,
        recent_games=team_schedule(team_id, season),
        members=team_members(team_id,season),
        signing_history=signing_history,
        trade_history=trade_history,
    )


@app.route("/teams/add", methods=["GET", "POST"])
def add_team():
    require_league_admin()
    if request.method == "POST":
        try:
            city, name, abbreviation, founded_year, arena = parse_team_form(request.form)
            execute(
                """
                INSERT INTO teams (city, name, abbreviation, founded_year, arena)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (city, name, abbreviation, founded_year, arena),
            )
            invalidate_all()
            flash("球队添加成功", "success")
            return redirect(url_for("teams"))
        except ValueError as exc:
            flash(str(exc), "danger")
        except pymysql.err.IntegrityError:
            flash("球队名称或缩写已存在", "danger")

    return render_template("team_form.html", team=None)


@app.route("/teams/edit/<int:team_id>", methods=["GET", "POST"])
def edit_team(team_id):
    if not can_edit_team(team_id):
        abort(403)
    team = get_team_or_404(team_id)
    if team["archived_at"]:
        abort(403)

    if request.method == "POST":
        try:
            city, name, abbreviation, founded_year, arena = parse_team_form(request.form)
            execute(
                """
                UPDATE teams
                SET city = %s, name = %s, abbreviation = %s,
                    founded_year = %s, arena = %s
                WHERE id = %s
                """,
                (city, name, abbreviation, founded_year, arena, team_id),
            )
            invalidate_all()
            flash("球队信息已更新", "success")
            return redirect(url_for("teams"))
        except ValueError as exc:
            flash(str(exc), "danger")
        except pymysql.err.IntegrityError:
            flash("球队名称或缩写已存在", "danger")

    return render_template("team_form.html", team=team)


@app.route("/teams/delete/<int:team_id>", methods=["POST"])
@app.post("/teams/<int:team_id>/archive")
def delete_team(team_id):
    require_league_admin()
    if get_team_or_404(team_id)["archived_at"]:
        return redirect(url_for("teams"))
    with transaction() as connection, connection.cursor() as cursor:
        lock_teams(cursor, team_id)
        require_roster_idle(cursor, team_id)
        cursor.execute("UPDATE teams SET archived_at=NOW() WHERE id=%s", (team_id,))
        cursor.execute(
            "UPDATE games SET status='cancelled' WHERE status='scheduled' AND (home_team_id=%s OR away_team_id=%s)",
            (team_id, team_id),
        )
        cursor.execute(
            """UPDATE trade_proposals SET status='cancelled', responded_at=NOW()
               WHERE status='pending' AND (proposer_team_id=%s OR receiver_team_id=%s)""",
            (team_id, team_id),
        )
    invalidate_all()
    flash("球队已归档，待开赛赛程和待审批交易已取消，历史记录保留。", "success")
    return redirect(url_for("teams"))


@app.route("/games")
def games():
    page, per_page = pagination_args()
    season = current_season()
    search = search_term()
    where_clause = "WHERE g.season = %s"
    params = [season]
    team_id = request.args.get("team", type=int)
    filtered_team = None
    if team_id is not None:
        filtered_team = get_team_or_404(team_id)
        where_clause += " AND (g.home_team_id = %s OR g.away_team_id = %s)"
        params.extend([team_id, team_id])
    if search:
        like = f"%{search}%"
        where_clause += """
            AND (
                ht.name LIKE %s OR ht.abbreviation LIKE %s
                OR at.name LIKE %s OR at.abbreviation LIKE %s
            )
        """
        params.extend([like, like, like, like])

    status_filter = request.args.get("status") or ""
    status_counts = {"scheduled": 0, "live": 0, "finished": 0, "cancelled": 0}
    for row in fetch_all(
        f"""
        SELECT g.status, COUNT(*) AS total
        FROM games g LEFT JOIN scorekeeper_business_dates bd ON bd.season=g.season
        JOIN teams ht ON ht.id = g.home_team_id
        JOIN teams at ON at.id = g.away_team_id
        {where_clause}
        GROUP BY g.status
        """,
        tuple(params),
    ):
        status_counts[str(row["status"])] = row["total"]

    if status_filter in {"finished", "live", "scheduled", "cancelled"}:
        where_clause += " AND g.status = %s"
        params.append(status_filter)

    total = fetch_one(
        f"""
        SELECT COUNT(*) AS total
        FROM games g LEFT JOIN scorekeeper_business_dates bd ON bd.season=g.season
        JOIN teams ht ON ht.id = g.home_team_id
        JOIN teams at ON at.id = g.away_team_id
        {where_clause}
        """,
        tuple(params),
    )["total"]
    games_data = fetch_all(
        f"""
        SELECT
            g.id,
            g.game_date,
            g.season,
            g.home_score,
            g.away_score,
            g.status,
            g.home_team_id,
            g.away_team_id,
            g.period,
            g.seconds_remaining,
            ht.name AS home_team_name,
            ht.abbreviation AS home_team_abbr,
            at.name AS away_team_name,
            at.abbreviation AS away_team_abbr
        FROM games g LEFT JOIN scorekeeper_business_dates bd ON bd.season=g.season
        JOIN teams ht ON ht.id = g.home_team_id
        JOIN teams at ON at.id = g.away_team_id
        {where_clause}
        ORDER BY CASE
            WHEN g.game_date=bd.business_date AND g.status<>'cancelled' THEN 0
            WHEN g.status IN ('scheduled','live') THEN 1
            WHEN g.status='finished' THEN 2 ELSE 3 END,
            CASE WHEN g.game_date=bd.business_date AND g.status<>'cancelled' THEN 0
                 WHEN g.status IN ('scheduled','live') THEN TO_DAYS(g.game_date)
                 ELSE -TO_DAYS(g.game_date) END,
            CASE g.status WHEN 'live' THEN 0 WHEN 'scheduled' THEN 1 ELSE 2 END,g.id
        LIMIT %s OFFSET %s
        """,
        (*params, per_page, (page - 1) * per_page),
    )
    total_pages = (total + per_page - 1) // per_page
    setting = fetch_one("SELECT business_date FROM scorekeeper_business_dates WHERE season=%s",(season,))
    return render_template(
        "games.html",
        games=games_data,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        season=season,
        seasons=SEASONS,
        search=search,
        filtered_team=filtered_team, business_date=setting["business_date"] if setting else None,
        status_counts=status_counts, status_filter=status_filter,
    )


def game_line_score(game_id):
    rows = fetch_all(
        """
        SELECT period, seconds_remaining, id, score_home, score_away
        FROM play_by_play
        WHERE game_id = %s AND voided_at IS NULL
        ORDER BY period ASC, seconds_remaining DESC, id ASC
        """,
        (game_id,),
    )
    closes: dict[int, dict] = {}
    for row in rows:
        closes[int(row["period"])] = row
    segments = []
    prev_home = prev_away = 0
    for period in sorted(closes):
        row = closes[period]
        home_total = int(row["score_home"] or 0)
        away_total = int(row["score_away"] or 0)
        segments.append(
            {
                "label": f"Q{period}" if period <= 4 else f"OT{period - 4}",
                "home": home_total - prev_home,
                "away": away_total - prev_away,
            }
        )
        prev_home, prev_away = home_total, away_total
    return segments


def game_box_totals(lines):
    total = {
        "points": 0, "rebounds": 0, "assists": 0, "steals": 0, "blocks": 0,
        "turnovers": 0, "fouls": 0, "plus_minus": 0,
        "fgm": 0, "fga": 0, "tpm": 0, "ftm": 0, "fta": 0,
        "seconds": 0,
    }
    for line in lines:
        for key in ("points", "rebounds", "assists", "steals", "blocks", "turnovers", "fouls", "plus_minus"):
            total[key] += int(line.get(key) or 0)
        total["fgm"] += int(line.get("field_goals_made") or 0)
        total["fga"] += int(line.get("field_goals_attempted") or 0)
        total["tpm"] += int(line.get("three_points_made") or 0)
        total["ftm"] += int(line.get("free_throws_made") or 0)
        total["fta"] += int(line.get("free_throws_attempted") or 0)
        total["seconds"] += int(line.get("seconds_played") or 0)
    total["fg_pct"] = round(total["fgm"] / total["fga"] * 100, 1) if total["fga"] else 0.0
    total["ft_pct"] = round(total["ftm"] / total["fta"] * 100, 1) if total["fta"] else 0.0
    total["minutes"] = round(total["seconds"] / 60)
    return total


@app.route("/games/<int:game_id>")
def game_detail(game_id):
    game = get_game_or_404(game_id)
    stat_lines = fetch_all(
        """
        SELECT
            s.*,
            p.name AS player_name,
            p.position,
            t.abbreviation AS team_abbr
        FROM player_game_stats s
        JOIN players p ON p.id = s.player_id
        JOIN teams t ON t.id = s.team_id
        WHERE s.game_id = %s
        ORDER BY s.points DESC, s.rebounds DESC
        """,
        (game_id,),
    )
    home_lines = [row for row in stat_lines if row["team_id"] == game["home_team_id"]]
    away_lines = [row for row in stat_lines if row["team_id"] == game["away_team_id"]]
    home_totals = game_box_totals(home_lines)
    away_totals = game_box_totals(away_lines)
    home_win = (game.get("home_score") or 0) > (game.get("away_score") or 0)
    return render_template(
        "game_detail.html",
        game=game,
        stats=stat_lines,
        home_lines=home_lines,
        away_lines=away_lines,
        home_totals=home_totals,
        away_totals=away_totals,
        line_score=game_line_score(game_id),
        home_win=home_win,
    )


@app.route("/games/<int:game_id>/play-by-play")
def play_by_play(game_id):
    game = get_game_or_404(game_id)
    events = fetch_all(
        """
        SELECT
            pbp.*,
            p.name AS player_name,
            a.name AS assist_name,
            t.abbreviation AS team_abbr
        FROM play_by_play pbp
        JOIN teams t ON t.id = pbp.team_id
        LEFT JOIN players p ON p.id = pbp.player_id
        LEFT JOIN players a ON a.id = pbp.assist_player_id
        WHERE pbp.game_id = %s
        ORDER BY pbp.period, pbp.seconds_remaining DESC, pbp.id
        """,
        (game_id,),
    )
    return render_template("play_by_play.html", game=game, events=events)


@app.route("/scorekeeper/calendar")
def scorekeeper_calendar():
    season = current_season()
    business_setting = fetch_one(
        """
        SELECT business_date, updated_at
        FROM scorekeeper_business_dates
        WHERE season = %s
        """,
        (season,),
    )
    games_data = fetch_all(
        """
        SELECT
            g.id,
            g.game_date,
            g.season,
            g.home_score,
            g.away_score,
            g.status,
            ht.name AS home_team_name,
            ht.abbreviation AS home_team_abbr,
            at.name AS away_team_name,
            at.abbreviation AS away_team_abbr
        FROM games g
        JOIN teams ht ON ht.id = g.home_team_id
        JOIN teams at ON at.id = g.away_team_id
        WHERE g.season = %s AND g.status <> 'cancelled'
        ORDER BY g.game_date, g.id
        """,
        (season,),
    )
    business_date = business_setting["business_date"] if business_setting else None
    for game in games_data:
        game.update(
            scorekeeper.date_context(
                {**game, "business_date": business_date}
            )
        )
    return render_template(
        "scorekeeper_calendar.html",
        games=ordered_games(games_data,business_date),
        season=season,
        seasons=SEASONS,
        business_date=business_date,
        business_date_updated_at=business_setting["updated_at"] if business_setting else None,
    )


@app.post("/scorekeeper/calendar/date")
@app.post("/league/business-date")
def league_business_date():
    require_league_admin()
    season = request.form.get("season", "")
    if season not in SEASONS:
        flash("赛季参数无效", "danger")
        return redirect(url_for("league_dashboard"))

    try:
        target_date = scorekeeper.as_date(request.form.get("business_date"), "比赛日")
        with transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT business_date
                    FROM scorekeeper_business_dates
                    WHERE season = %s
                    FOR UPDATE
                    """,
                    (season,),
                )
                setting = cursor.fetchone()
                if setting is None:
                    raise scorekeeper.ScorekeeperError("当前赛季尚未初始化比赛日")

                current_date = scorekeeper.as_date(setting["business_date"])
                if target_date < current_date:
                    raise scorekeeper.ScorekeeperError("比赛日只能向后推进，不能回退")

                cursor.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM games
                    WHERE season = %s AND game_date = %s AND status <> 'cancelled'
                    """,
                    (season, target_date),
                )
                if cursor.fetchone()["total"] == 0:
                    raise scorekeeper.ScorekeeperError("所选日期没有本赛季比赛，请选择赛程中的日期")

                cursor.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM games
                    WHERE season = %s
                      AND game_date < %s
                      AND status IN ('scheduled', 'live')
                    """,
                    (season, target_date),
                )
                unfinished_before = cursor.fetchone()["total"]
                if unfinished_before:
                    raise scorekeeper.ScorekeeperError(
                        f"目标日期之前仍有 {unfinished_before} 场比赛未结束，不能推进比赛日"
                    )

                cursor.execute(
                    """
                    UPDATE scorekeeper_business_dates
                    SET business_date = %s
                    WHERE season = %s
                    """,
                    (target_date, season),
                )
    except scorekeeper.ScorekeeperError as exc:
        flash(exc.message, "danger")
    else:
        invalidate_all()
        flash(f"当前比赛日已更新为 {target_date.isoformat()}", "success")

    return redirect(url_for("league_dashboard", season=season), code=303)


@app.route("/scorekeeper/game/<int:game_id>")
def scorekeeper_game(game_id):
    return render_template("scorekeeper_game.html", **scorekeeper.game_state(game_id))


@app.route("/api/scorekeeper/game/<int:game_id>")
def scorekeeper_game_state(game_id):
    response = jsonify(scorekeeper.game_state(game_id))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/scorekeeper/game/<int:game_id>/start", methods=["POST"])
def scorekeeper_game_start(game_id):
    try:
        result = scorekeeper.start_game(game_id)
    except scorekeeper.ScorekeeperError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/scorekeeper/game/<int:game_id>/lineup", methods=["POST"])
def scorekeeper_game_lineup(game_id):
    payload = json_object()
    try:
        result = scorekeeper.set_lineup(game_id, payload)
    except scorekeeper.ScorekeeperError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/scorekeeper/game/<int:game_id>/clock", methods=["POST"])
def scorekeeper_game_clock(game_id):
    payload = json_object()
    try:
        result = scorekeeper.update_clock(game_id, payload)
    except scorekeeper.ScorekeeperError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/scorekeeper/game/<int:game_id>/update", methods=["POST"])
def scorekeeper_game_update(game_id):
    payload = json_object()
    try:
        result = scorekeeper.record_event(game_id, payload)
    except scorekeeper.ScorekeeperError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/scorekeeper/game/<int:game_id>/end", methods=["POST"])
def scorekeeper_game_end(game_id):
    try:
        result = scorekeeper.finish_game(game_id, user_id=g.user["id"], note="记分员按当前已录入比分确认完赛。")
    except scorekeeper.ScorekeeperError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/scorekeeper/game/<int:game_id>/events/<int:event_id>/reverse")
def scorekeeper_game_reverse(game_id, event_id):
    try:
        result = scorekeeper.reverse_event(game_id, event_id, g.user["id"], json_object().get("reason"))
    except scorekeeper.ScorekeeperError as exc:
        return jsonify({"error": exc.message}), exc.status_code
    return jsonify(result), {"Cache-Control": "no-store"}


def _error_page(code, title, message, hint=None, kicker=None):
    user = getattr(g, "user", None)
    if user and user["role"] == ROLE_LEAGUE_ADMIN:
        home = url_for("league_dashboard")
    else:
        home = url_for("index")
    return (
        render_template(
            "error.html",
            code=code,
            title=title,
            message=message,
            hint=hint,
            kicker=kicker or "Courtside error",
            home_url=home,
        ),
        code,
    )


@app.errorhandler(403)
def handle_forbidden(exc):
    return _error_page(
        403,
        "这一步需要另一种身份",
        "当前账号看不到这个页面，或这个操作只对特定角色开放。侧栏里灰掉的入口同样属于权限范围之外。",
        getattr(exc, "description", None)
        if getattr(exc, "description", None) and not str(getattr(exc, "description", "")).startswith("You don't")
        else "联赛管理员可以只读查看球队薪资与选秀板；写入操作请使用对应球队的管理层账号。",
        kicker="Access restricted",
    )


@app.errorhandler(404)
def handle_not_found(exc):
    return _error_page(
        404,
        "没有找到这个页面",
        "链接可能已经失效，或这条记录已被删除。可以从工作台重新进入对应模块。",
        None,
        kicker="Not found",
    )


if __name__ == "__main__":
    app.run()
