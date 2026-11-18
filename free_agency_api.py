from decimal import Decimal, ROUND_HALF_UP

from flask import Blueprint, jsonify, request

from caching import invalidate_all
from cba import validate_and_calculate_offer
from constants import SEASONS
from db import fetch_all, transaction
from decision_analysis import analyze_signing
from roster import contract_year_salaries, lock_player, lock_teams, sign_free_agent, waive_roster_player
from roster_recommendations import signing_recommendations
from security import management_team_id
from validation import BusinessError, bounded_float, bounded_int, choice, json_object, season_value


blueprint = Blueprint("free_agency_api", __name__)
TIERS = ("Tier 1", "MLE Target", "Vet Min")


@blueprint.post("/api/free-agency/recommendations")
def suggest_signings():
    team_id = management_team_id()
    season = season_value(json_object().get("season", SEASONS[-1]))
    return jsonify(signing_recommendations(team_id, season)), 200, {"Cache-Control": "no-store"}


@blueprint.post("/api/free-agency/analysis")
def signing_analysis():
    team_id = management_team_id()
    data = json_object()
    target_id = bounded_int(data.get("target_id"), "目标")
    terms = {
        "season": season_value(data.get("season", SEASONS[-1])),
        "years": bounded_int(data.get("years"), "年限", 1, 5),
        "year1_salary": bounded_int(data.get("year1_salary"), "首年薪资", 0),
        "exception_type": choice(data.get("exception_type", "Standard"), "合同特例", ("Standard", "Full MLE", "BAE")),
    }
    return jsonify(analyze_signing(team_id, target_id, terms)), 200, {"Cache-Control": "no-store"}


@blueprint.route("/api/free-agency/targets", methods=["GET", "POST"])
def targets():
    team_id = management_team_id()
    if request.method == "POST":
        payload = json_object()
        player_id = bounded_int(payload.get("player_id"), "球员")
        tier = choice(payload.get("tier"), "优先级", TIERS)
        with transaction() as connection, connection.cursor() as cursor:
            lock_teams(cursor, team_id)
            player = lock_player(cursor, player_id)
            if player["team_id"] is not None:
                raise BusinessError("只能添加自由球员", 409)
            cursor.execute(
                "SELECT id FROM free_agency_targets WHERE team_id = %s AND player_id = %s",
                (team_id, player_id),
            )
            if cursor.fetchone():
                raise BusinessError("该球员已在目标池中", 409)
            cursor.execute(
                """
                INSERT INTO free_agency_targets (team_id, player_id, tier, priority_order)
                SELECT %s, %s, %s, COALESCE(MAX(priority_order), 0) + 1
                FROM free_agency_targets WHERE team_id = %s
                """,
                (team_id, player_id, tier, team_id),
            )
        return jsonify({"message": "目标已添加"}), 201

    season = season_value(request.args.get("season", SEASONS[-1]))
    rows = fetch_all(
        """
        SELECT fat.id, fat.player_id, fat.tier, fat.priority_order,
               p.name AS player_name, p.position, 'FREE' AS team_name, 'FA' AS abbreviation,
               co.id AS offer_id, co.years, co.year1_salary, co.annual_increase_pct,
               co.team_option, co.player_option, co.status, co.version AS offer_version,
               co.season AS offer_season, co.exception_type
        FROM free_agency_targets fat
        JOIN players p ON p.id = fat.player_id
        LEFT JOIN contract_offers co ON co.id = (
            SELECT id FROM contract_offers WHERE target_id = fat.id AND status = 'Draft'
              AND (season = %s OR season IS NULL)
            ORDER BY season IS NOT NULL DESC, updated_at DESC, id DESC LIMIT 1
        )
        WHERE fat.team_id = %s AND p.team_id IS NULL AND p.archived_at IS NULL
        ORDER BY fat.tier, fat.priority_order, fat.id
        """,
        (season, team_id),
    )
    return jsonify(rows)


@blueprint.delete("/api/free-agency/targets/<int:target_id>")
def delete_target(target_id):
    team_id = management_team_id()
    with transaction() as connection, connection.cursor() as cursor:
        lock_teams(cursor, team_id)
        cursor.execute(
            """
            SELECT 1 FROM contract_offers o JOIN free_agency_targets t ON t.id = o.target_id
            WHERE t.id = %s AND t.team_id = %s AND o.status = 'Signed' LIMIT 1
            """, (target_id, team_id),
        )
        if cursor.fetchone():
            raise BusinessError("该目标有关联合同，需保留已签报价", 409)
        cursor.execute(
            "DELETE FROM free_agency_targets WHERE id = %s AND team_id = %s",
            (target_id, team_id),
        )
        if not cursor.rowcount:
            raise BusinessError("目标不存在", 404)
    return jsonify({"message": "目标已移除"})


@blueprint.post("/api/free-agency/offers")
def save_offer():
    team_id = management_team_id()
    payload = json_object()
    target_id = bounded_int(payload.get("target_id"), "目标")
    years = bounded_int(payload.get("years", 1), "年限", 1, 5)
    salary = bounded_int(payload.get("year1_salary", 0), "首年薪资", 0)
    increase = Decimal(str(bounded_float(payload.get("annual_increase_pct", 0.05), "年涨幅", 0, 0.08))).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP,
    )
    season = season_value(payload.get("season", SEASONS[-1]))
    exception = choice(payload.get("exception_type", "Standard"), "合同特例", ("Standard", "Full MLE", "BAE"))
    team_option = payload.get("team_option", False)
    player_option = payload.get("player_option", False)
    if not isinstance(team_option, bool) or not isinstance(player_option, bool):
        raise BusinessError("合同选项必须为布尔值")
    if team_option and player_option:
        raise BusinessError("同一合同不能同时选择球队选项和球员选项")
    if payload.get("status", "Draft") != "Draft":
        raise BusinessError("报价仅支持保存为草稿")

    with transaction() as connection, connection.cursor() as cursor:
        lock_teams(cursor, team_id)
        cursor.execute(
            "SELECT player_id FROM free_agency_targets WHERE id = %s AND team_id = %s",
            (target_id, team_id),
        )
        target = cursor.fetchone()
        if target is None:
            raise BusinessError("目标不存在", 404)
        player = lock_player(cursor, target["player_id"])
        if player["team_id"] is not None:
            raise BusinessError("球员已不在自由市场", 409)
        validation = validate_and_calculate_offer(
            team_id, player["id"], {
                "season": season, "year1_salary": salary, "exception_type": exception,
            }, cursor=cursor,
        )
        # The sandbox lets managers keep a draft and see how it violates the rules.
        cursor.execute(
            "SELECT id, version FROM contract_offers WHERE target_id = %s AND season = %s AND status = 'Draft' ORDER BY id DESC LIMIT 1",
            (target_id, season),
        )
        offer = cursor.fetchone()
        values = (years, salary, increase, team_option, player_option)
        if offer:
            cursor.execute(
                """
                UPDATE contract_offers
                SET years = %s, year1_salary = %s, annual_increase_pct = %s,
                    team_option = %s, player_option = %s, exception_type = %s, version = version + 1
                WHERE id = %s
                """,
                (*values, exception, offer["id"]),
            )
            offer_id, version = offer["id"], offer["version"] + 1
        else:
            cursor.execute(
                """
                INSERT INTO contract_offers
                    (years, year1_salary, annual_increase_pct, team_option, player_option, target_id, season, exception_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (*values, target_id, season, exception),
            )
            offer_id, version = cursor.lastrowid, 1
    return jsonify({**validation, "offer_id": offer_id, "offer_version": version,
                    "year_salaries": contract_year_salaries(salary, years, increase)})


@blueprint.post("/api/free-agency/reorder")
def reorder_targets():
    team_id = management_team_id()
    items = json_object().get("items")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise BusinessError("排序内容必须是目标对象列表")
    changes = {
        bounded_int(item.get("target_id"), "目标"): (
            choice(item.get("tier"), "优先级", TIERS),
            bounded_int(item.get("priority_order"), "排序", 0),
        )
        for item in items
    }
    if len(changes) != len(items):
        raise BusinessError("排序目标不能重复")
    with transaction() as connection, connection.cursor() as cursor:
        lock_teams(cursor, team_id)
        cursor.execute("SELECT id FROM free_agency_targets WHERE team_id = %s", (team_id,))
        if changes.keys() - {row["id"] for row in cursor.fetchall()}:
            raise BusinessError("目标不存在", 404)
        if changes:
            cursor.executemany(
                """
                UPDATE free_agency_targets SET tier = %s, priority_order = %s
                WHERE id = %s AND team_id = %s
                """,
                [(tier, priority, target_id, team_id) for target_id, (tier, priority) in changes.items()],
            )
    return jsonify({"message": "优先级已更新"})


@blueprint.post("/api/players/<int:player_id>/sign")
def sign_player(player_id):
    team_id = management_team_id()
    payload = json_object()
    if bounded_int(payload.get("target_team_id", team_id), "球队") != team_id:
        raise BusinessError("管理层只能为自己所属的球队签约球员", 403)
    season = season_value(payload.get("season", SEASONS[-1]))
    if "offer_id" in payload:
        offer_id = bounded_int(payload["offer_id"], "报价")
        version = bounded_int(payload.get("offer_version"), "报价版本")
        sign_free_agent(team_id, player_id, None, None, season, offer_id=offer_id, offer_version=version)
    else:
        salary = bounded_int(payload.get("salary"), "年薪", 1)
        years = bounded_int(payload.get("years", 1), "年限", 1, 5)
        sign_free_agent(team_id, player_id, salary, years, season)
    invalidate_all()
    return jsonify({"message": "签约成功"})


@blueprint.post("/api/players/<int:player_id>/waive")
def waive_player(player_id):
    waive_roster_player(management_team_id(), player_id)
    invalidate_all()
    return jsonify({"message": "裁员成功"})
