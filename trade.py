import json

from db import fetch_all, fetch_one, transaction
from roster import available_jersey, lock_player, lock_teams, season_salary_sql, team_payroll
from validation import BusinessError


def salary_matching_max(outgoing, over_cap, cap_space):
    if not over_cap:
        return cap_space + 250000
    if outgoing <= 7_500_000:
        return outgoing * 2 + 250_000
    if outgoing <= 29_000_000:
        return outgoing + 7_500_000
    return outgoing * 1.25 + 250_000


def _fetch_all_with_cursor(cursor, sql, params=()):
    cursor.execute(sql, params)
    return cursor.fetchall()


def _fetch_one_with_cursor(cursor, sql, params=()):
    cursor.execute(sql, params)
    return cursor.fetchone()


def get_team_summary(team_id, player_ids, season, cursor=None):
    players = []
    total_outgoing = 0
    if player_ids:
        placeholders = ",".join(["%s"] * len(player_ids))
        sql = f"""
            SELECT p.id, p.name, p.team_id, COALESCE((
                SELECT SUM({season_salary_sql()} + c.incentives) FROM contracts c
                WHERE c.player_id = p.id AND c.team_id = p.team_id AND c.status = 'Active'
                  AND c.start_year <= %s AND c.end_year >= %s
            ), 0) AS salary
            FROM players p
            WHERE p.id IN ({placeholders}) AND p.team_id = %s
            """
        year = int(season.split("-", 1)[0])
        params = (year, year, year, *player_ids, team_id)
        players = (
            _fetch_all_with_cursor(cursor, sql, params)
            if cursor
            else fetch_all(sql, params)
        )
        total_outgoing = sum(int(player["salary"]) for player in players)

    cap_sql = "SELECT * FROM salary_caps WHERE season = %s"
    cap = (
        _fetch_one_with_cursor(cursor, cap_sql, (season,))
        if cursor
        else fetch_one(cap_sql, (season,))
    )
    current_payroll = team_payroll(team_id, season, cursor)
    cap_amount = int(cap["cap_amount"]) if cap else 0
    over_cap = current_payroll > cap_amount
    cap_space = cap_amount - current_payroll

    roster_sql = "SELECT COUNT(*) AS total FROM players WHERE team_id = %s"
    roster_count = (
        _fetch_one_with_cursor(cursor, roster_sql, (team_id,))
        if cursor
        else fetch_one(roster_sql, (team_id,))
    )

    return {
        "team_id": team_id,
        "players": players,
        "outgoing": total_outgoing,
        "payroll": current_payroll,
        "cap_amount": cap_amount,
        "has_cap_data": cap is not None,
        "cap_space": cap_space,
        "over_cap": over_cap,
        "roster_count": int(roster_count["total"]) if roster_count else 0,
    }


def validate_trade(payload, cursor=None):
    team1_id = int(payload["team1_id"])
    team2_id = int(payload["team2_id"])
    team1_players = [int(value) for value in payload.get("team1_outgoing_player_ids", [])]
    team2_players = [int(value) for value in payload.get("team2_outgoing_player_ids", [])]
    season = payload.get("season", "2025-26")

    team1 = get_team_summary(team1_id, team1_players, season, cursor)
    team2 = get_team_summary(team2_id, team2_players, season, cursor)
    reasons = []
    if not team1["has_cap_data"] or not team2["has_cap_data"]:
        reasons.append("该赛季缺少工资帽数据")

    if team1_id == team2_id:
        reasons.append("交易双方不能是同一支球队")
    if not team1_players or not team2_players:
        reasons.append("交易双方都必须至少选择一名球员")
    if len(team1_players) != len(set(team1_players)):
        reasons.append("发起方球员不能重复选择")
    if len(team2_players) != len(set(team2_players)):
        reasons.append("接收方球员不能重复选择")
    if len(team1["players"]) != len(set(team1_players)):
        reasons.append("发起方包含不属于本队或不存在的球员")
    if len(team2["players"]) != len(set(team2_players)):
        reasons.append("接收方包含不属于该队或不存在的球员")

    team1_max_incoming = salary_matching_max(
        team1["outgoing"],
        team1["over_cap"],
        team1["cap_space"],
    )
    team2_max_incoming = salary_matching_max(
        team2["outgoing"],
        team2["over_cap"],
        team2["cap_space"],
    )

    if team2["outgoing"] > team1_max_incoming:
        reasons.append(
            f"Team 1 换入薪资超出 CBA 配平上限 ${team1_max_incoming:,}"
        )
    if team1["outgoing"] > team2_max_incoming:
        reasons.append(
            f"Team 2 换入薪资超出 CBA 配平上限 ${team2_max_incoming:,}"
        )

    team1_after_roster = team1["roster_count"] - len(team1["players"]) + len(team2_players)
    team2_after_roster = team2["roster_count"] - len(team2["players"]) + len(team1_players)
    if team1_after_roster > 15:
        reasons.append(f"Team 1 交易后人数达到 {team1_after_roster} 人，超出 15 人限制")
    if team2_after_roster > 15:
        reasons.append(f"Team 2 交易后人数达到 {team2_after_roster} 人，超出 15 人限制")

    return {
        "is_valid": len(reasons) == 0,
        "team1_summary": {
            "outgoing": team1["outgoing"],
            "incoming": team2["outgoing"],
            "cap_space_after": team1["cap_space"] - team2["outgoing"] + team1["outgoing"],
        },
        "team2_summary": {
            "outgoing": team2["outgoing"],
            "incoming": team1["outgoing"],
            "cap_space_after": team2["cap_space"] - team1["outgoing"] + team2["outgoing"],
        },
        "reasons": reasons,
    }


def execute_trade_with_cursor(cursor, payload):
    team1_id = int(payload["team1_id"])
    team2_id = int(payload["team2_id"])
    team1_players = [int(value) for value in payload.get("team1_outgoing_player_ids", [])]
    team2_players = [int(value) for value in payload.get("team2_outgoing_player_ids", [])]

    try:
        lock_teams(cursor, team1_id, team2_id)
        locked_players = {
            player_id: lock_player(cursor, player_id)
            for player_id in sorted(set(team1_players + team2_players))
        }
    except BusinessError as exc:
        return {"is_valid": False, "reasons": [str(exc)]}

    validation = validate_trade(payload, cursor)
    if not validation["is_valid"]:
        return validation

    for player_ids, destination in ((team1_players, team2_id), (team2_players, team1_id)):
        for player_id in player_ids:
            player = locked_players[player_id]
            jersey = available_jersey(cursor, player["jersey_number"], destination, exclude_player=player_id)
            cursor.execute(
                "UPDATE players SET team_id = %s, jersey_number = %s WHERE id = %s",
                (destination, jersey, player_id),
            )
            cursor.execute(
                "UPDATE contracts SET team_id = %s WHERE player_id = %s AND status = 'Active'",
                (destination, player_id),
            )
    cursor.execute(
        """
        INSERT INTO trade_history
            (team1_id, team2_id, team1_outgoing, team2_outgoing)
        VALUES (%s, %s, %s, %s)
        """,
        (team1_id, team2_id, json.dumps(team1_players), json.dumps(team2_players)),
    )
    validation["executed"] = True
    return validation


def execute_trade(payload):
    with transaction() as connection:
        with connection.cursor() as cursor:
            return execute_trade_with_cursor(cursor, payload)
