import json
from uuid import uuid4

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from analysis_model import ACTIONS, PROMPT_VERSION, configured_model, generate_content, rules_report
from auth import ROLE_LEAGUE_ADMIN, ROLE_MANAGEMENT
from db import fetch_all, fetch_one, transaction
from game_analysis import AVAILABILITY_LABELS, METRICS_VERSION, awaiting_starters, encode, fingerprint, get_game, load_evidence
from security import can_edit_team
from validation import BusinessError, bounded_int, choice


blueprint = Blueprint("analysis_reports", __name__)
TITLES = {"review": "赛后复盘", "scout": "赛前分析"}


def _context(game_id, kind=None, saved_team=None):
    if kind is not None and kind not in TITLES:
        abort(404)
    with transaction() as connection, connection.cursor() as cursor:
        game = get_game(cursor, game_id)
    if game is None:
        abort(404)
    teams = {game["home_team_id"]: game["home_team_name"], game["away_team_id"]: game["away_team_name"]}
    if g.user["role"] not in {ROLE_LEAGUE_ADMIN, ROLE_MANAGEMENT}:
        abort(403)
    default_team = g.user.get("team_id")
    if default_team not in teams:
        default_team = game["home_team_id"]
    team_id = saved_team if saved_team is not None else request.args.get("team", default_team, type=int)
    if kind == "scout" and g.user["role"] == ROLE_MANAGEMENT and team_id != g.user.get("team_id"):
        abort(403, "仅能查看本队视角的赛前分析")
    if team_id not in teams:
        abort(404, "该球队未参加本场比赛")
    return game, team_id, teams


def _history(game_id, team_id, kind):
    return fetch_all(
        """
        SELECT r.id, r.source, r.created_at, r.completed_at
        FROM game_analysis_reports r JOIN game_analysis_snapshots s ON s.id = r.snapshot_id
        WHERE s.game_id = %s AND s.team_id = %s AND s.kind = %s
        ORDER BY r.id DESC LIMIT 20
        """,
        (game_id, team_id, kind),
    )


def _claim(snapshot, generator, retry):
    token = uuid4().hex
    with transaction() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO game_analysis_snapshots
                (game_id, team_id, kind, fingerprint, metrics_version, evidence)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)
            """,
            (snapshot["game_id"], snapshot["team_id"], snapshot["kind"],
             fingerprint(snapshot), METRICS_VERSION, encode(snapshot)),
        )
        snapshot_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO game_analysis_reports
                (snapshot_id, generator, prompt_version, attempt_token, lease_until)
            VALUES (%s, %s, %s, %s, NOW(6) + INTERVAL 60 SECOND)
            ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)
            """,
            (snapshot_id, generator, PROMPT_VERSION, token),
        )
        report_id = cursor.lastrowid
        cursor.execute(
            """
            SELECT attempt_token, completed_at, source, lease_until <= NOW(6) AS expired
            FROM game_analysis_reports WHERE id = %s FOR UPDATE
            """,
            (report_id,),
        )
        existing = cursor.fetchone()
        if existing["attempt_token"] == token:
            return report_id, token
        stale = existing["completed_at"] is None and existing["expired"]
        retry_model = retry and existing["source"] == "rules" and generator != "rules"
        if not (stale or retry_model):
            return report_id, None
        cursor.execute(
            """
            UPDATE game_analysis_reports
            SET attempt_token = %s, lease_until = NOW(6) + INTERVAL 60 SECOND,
                source = NULL, content = NULL, completed_at = NULL
            WHERE id = %s
            """,
            (token, report_id),
        )
    return report_id, token


def _render(game, team_id, teams, snapshot, report=None):
    kind = snapshot["kind"]
    editable_teams = [id for id in teams if can_edit_team(id)]
    availability_players = []
    if kind == "scout" and awaiting_starters(game) and editable_teams:
        placeholders = ", ".join(["%s"] * len(editable_teams))
        availability_players = fetch_all(
            f"""
            SELECT p.id, p.name, p.team_id, p.position, a.status, s.player_id IS NOT NULL AS is_starter
            FROM players p LEFT JOIN game_player_availability a
                ON a.game_id = %s AND a.player_id = p.id AND a.team_id = p.team_id
            LEFT JOIN game_starters s ON s.game_id = %s AND s.player_id = p.id AND s.team_id = p.team_id
            WHERE p.team_id IN ({placeholders}) ORDER BY p.team_id, p.name
            """,
            (game["id"], game["id"], *editable_teams),
        )
    return render_template(
        "game_analysis.html", game=game, teams=teams, team_id=team_id,
        kind=kind, title=TITLES[kind], snapshot=snapshot, report=report,
        evidence={item["id"]: item for item in snapshot["evidence"]}, actions=ACTIONS,
        history=_history(game["id"], team_id, kind),
        model_configured=bool(configured_model()),
        can_generate=kind == "scout" or game["status"] == "finished",
        can_publish_availability=game["status"] == "scheduled",
        availability_players=availability_players, availability_labels=AVAILABILITY_LABELS,
        preview=rules_report([item for item in snapshot["evidence"] if item.get("recommendation")]),
    ), {"Cache-Control": "private, no-store"} if kind == "scout" else {}


@blueprint.post("/games/<int:game_id>/availability")
def publish_availability(game_id):
    _, team_id, teams = _context(game_id, "scout")
    player_id = bounded_int(request.form.get("player_id"), "球员")
    status = choice(request.form.get("status"), "出勤状态", {*AVAILABILITY_LABELS, "unpublished"})
    with transaction() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT status FROM games WHERE id = %s FOR UPDATE", (game_id,))
        game = cursor.fetchone()
        if not game or game["status"] != "scheduled":
            raise BusinessError("仅能在开赛前更新出勤公告", 409)
        cursor.execute("SELECT team_id FROM players WHERE id = %s FOR UPDATE", (player_id,))
        player = cursor.fetchone()
        if not player or player["team_id"] not in teams or not can_edit_team(player["team_id"]):
            abort(403)
        if status in {"injured", "rest"}:
            cursor.execute("SELECT 1 FROM game_starters WHERE game_id = %s AND player_id = %s AND team_id = %s",
                           (game_id, player_id, player["team_id"]))
            if cursor.fetchone():
                flash("该球员在首发名单中，请更换首发后再更新出勤状态。", "warning")
                return redirect(url_for(".overview", game_id=game_id, kind="scout", team=team_id), code=303)
        if status == "unpublished":
            cursor.execute("DELETE FROM game_player_availability WHERE game_id = %s AND player_id = %s AND team_id = %s",
                           (game_id, player_id, player["team_id"]))
        else:
            cursor.execute(
                """
                INSERT INTO game_player_availability (game_id, player_id, team_id, status)
                VALUES (%s, %s, %s, %s) AS new_status
                ON DUPLICATE KEY UPDATE team_id = new_status.team_id, status = new_status.status
                """,
                (game_id, player_id, player["team_id"], status),
            )
    flash("本场出勤公告已更新。", "success")
    return redirect(url_for(".overview", game_id=game_id, kind="scout", team=team_id), code=303)


@blueprint.post("/games/<int:game_id>/starters")
def publish_starters(game_id):
    _, perspective, teams = _context(game_id, "scout")
    team_id = bounded_int(request.form.get("team_id"), "球队")
    if team_id not in teams or not can_edit_team(team_id):
        abort(403)
    back = url_for(".overview", game_id=game_id, kind="scout", team=perspective)
    raw_ids = request.form.getlist("player_ids")
    if len(raw_ids) != 5:
        flash("请勾选五名首发球员。", "warning")
        return redirect(back, code=303)
    player_ids = [bounded_int(raw_id, "球员") for raw_id in raw_ids]
    with transaction() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT status, starters_initialized FROM games WHERE id = %s FOR UPDATE", (game_id,))
        game = cursor.fetchone()
        if not game or not awaiting_starters(game):
            raise BusinessError("本场首发已载入或比赛已结束，无法更改首发名单。", 409)
        cursor.execute(
            """
            SELECT p.id FROM players p
            LEFT JOIN game_player_availability a
                ON a.game_id = %s AND a.player_id = p.id AND a.team_id = p.team_id
            WHERE p.team_id = %s AND p.id IN (%s, %s, %s, %s, %s)
              AND (a.status IS NULL OR a.status NOT IN ('injured', 'rest'))
            FOR UPDATE
            """,
            (game_id, team_id, *player_ids),
        )
        if len(cursor.fetchall()) != 5:
            flash("首发必须是五名不同的本队球员，已公告伤停或轮休的球员不能入选。", "warning")
            return redirect(back, code=303)
        cursor.execute("DELETE FROM game_starters WHERE game_id = %s AND team_id = %s", (game_id, team_id))
        cursor.executemany(
            """
            INSERT INTO game_starters (game_id, player_id, team_id) VALUES (%s, %s, %s) AS published
            ON DUPLICATE KEY UPDATE team_id = published.team_id
            """,
            [(game_id, player_id, team_id) for player_id in player_ids],
        )
    flash("首发五人已发布，记分员开赛时将自动载入。", "success")
    return redirect(back, code=303)


@blueprint.get("/games/<int:game_id>/analysis/<kind>")
def overview(game_id, kind):
    game, team_id, teams = _context(game_id, kind)
    return _render(game, team_id, teams, load_evidence(game_id, team_id, kind))


@blueprint.get("/api/games/<int:game_id>/analysis/<kind>/evidence")
def evidence_api(game_id, kind):
    _, team_id, _ = _context(game_id, kind)
    return jsonify(load_evidence(game_id, team_id, kind)), {"Cache-Control": "private, no-store"} if kind == "scout" else {}


@blueprint.post("/games/<int:game_id>/analysis/<kind>")
def generate(game_id, kind):
    _, team_id, _ = _context(game_id, kind)
    snapshot = load_evidence(game_id, team_id, kind)
    if not snapshot["evidence"]:
        raise BusinessError("暂时没有可用于分析的已完赛样本", 409)
    model = configured_model()
    report_id, token = _claim(snapshot, model or "rules", request.form.get("retry") == "1")
    if token:
        source, report = generate_content(snapshot, model)
        with transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE game_analysis_reports SET source = %s, content = %s, completed_at = NOW()
                WHERE id = %s AND attempt_token = %s AND completed_at IS NULL
                """,
                (source, encode(report), report_id, token),
            )
    return redirect(url_for(".report_detail", game_id=game_id, report_id=report_id), code=303)


@blueprint.get("/games/<int:game_id>/analysis/reports/<int:report_id>")
def report_detail(game_id, report_id):
    report = fetch_one(
        """
        SELECT r.id, r.source, r.content, r.generator, r.completed_at,
               r.lease_until <= NOW(6) AS expired, s.team_id, s.kind, s.evidence
        FROM game_analysis_reports r JOIN game_analysis_snapshots s ON s.id = r.snapshot_id
        WHERE r.id = %s AND s.game_id = %s
        """,
        (report_id, game_id),
    )
    if report is None:
        abort(404)
    game, team_id, teams = _context(game_id, report["kind"], saved_team=report["team_id"])
    report["content"] = json.loads(report["content"]) if report["content"] else None
    return _render(game, team_id, teams, json.loads(report["evidence"]), report)
