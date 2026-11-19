from collections import Counter

from basketball_fit import player_profiles
from db import transaction
from roster import max_contract_salary
from roster_rating import rate_roster_change
from roster_recommendations import SHORTLIST_LIMIT, TRIAL_SALARY, _recommend, _signing_options, trade_candidates
from team_planning import _budget, _contracts, _projection
from trade import validate_trade
from validation import BusinessError


def _trade_contracts(cursor, trade, base):
    outgoing = set(trade["team1_outgoing_player_ids"])
    incoming = set(trade["team2_outgoing_player_ids"])
    return [
        *[row for row in base if row["player_id"] not in outgoing],
        *[row for row in _contracts(cursor, trade["team2_id"]) if row["player_id"] in incoming],
    ]


def _new_contract(season, position, salary):
    start = int(season.split("-")[0])
    return {
        "position": position, "start_year": start, "end_year": start,
        "annual_salary": salary, "annual_increase_pct": 0, "incentives": 0,
        "team_option": False, "player_option": False,
    }


def check_followup_after_trade(cursor, trade, followup):
    team_id, season = trade["team1_id"], trade["season"]
    cursor.execute("SELECT id, name, age, team_id FROM players WHERE id = %s AND archived_at IS NULL", (followup["player_id"],))
    player = cursor.fetchone()
    if player is None or player["team_id"] is not None:
        raise BusinessError("计划签约的球员已不在自由市场，请重新生成方案", 409)
    cursor.execute("SELECT COUNT(*) AS total FROM players WHERE team_id=%s AND archived_at IS NULL", (team_id,))
    count = cursor.fetchone()["total"] - len(trade["team1_outgoing_player_ids"]) + len(trade["team2_outgoing_player_ids"])
    if count >= 15:
        raise BusinessError("交易后没有可用于签约的阵容名额", 409)
    cursor.execute("SELECT cap_amount, luxury_tax_line FROM salary_caps WHERE season = %s", (season,))
    cap = cursor.fetchone()
    salary = followup["salary"]
    if cap is None or salary < 1 or salary > max_contract_salary(cap["cap_amount"], player["age"]):
        raise BusinessError("当前已无法按计划年薪签约，请重新生成方案", 409)
    traded = _trade_contracts(cursor, trade, _contracts(cursor, team_id))
    total = _projection(traded, int(season.split("-")[0]))["payroll"] + salary
    if total > cap["luxury_tax_line"]:
        raise BusinessError("交易后的预算不足以完成计划签约，请重新生成方案", 409)


def recommend_packages(team_id, season):
    trades, games, priorities, message = trade_candidates(team_id, season, limit=18, reserve_salary=TRIAL_SALARY)
    if not trades:
        return _recommend("roster_packages", [], games, priorities, message)
    year = int(season.split("-")[0])
    candidates = []
    with transaction() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT cap_amount, luxury_tax_line FROM salary_caps WHERE season=%s", (season,))
        cap = cursor.fetchone()
        if cap is None:
            return _recommend("roster_packages", [], games, priorities, "该赛季缺少工资帽数据。")
        incoming_ids = sorted({player_id for row in trades for player_id in row["payload"]["team2_outgoing_player_ids"]})
        placeholders = ",".join(["%s"] * len(incoming_ids))
        cursor.execute(
            f"""SELECT id, name, position, age, team_id FROM players
                WHERE archived_at IS NULL AND (team_id = %s OR team_id IS NULL OR id IN ({placeholders})) ORDER BY id""",
            (team_id, *incoming_ids),
        )
        profiles = player_profiles(cursor, cursor.fetchall(), season)
        by_id = {row["id"]: row for row in profiles}
        own = [row for row in profiles if row["team_id"] == team_id]
        baseline = _contracts(cursor, team_id)
        rival_count = Counter()
        used_signings = set()
        previously_used = []
        for trade_plan in trades:
            trade = trade_plan["payload"]
            if rival_count[trade["team2_id"]] >= 2:
                continue
            result = validate_trade(trade, cursor)
            if not result["is_valid"]:
                continue
            after_trade = _trade_contracts(cursor, trade, baseline)
            remaining = cap["luxury_tax_line"] - _projection(after_trade, year)["payroll"]
            if remaining < TRIAL_SALARY:
                continue
            sent = set(trade["team1_outgoing_player_ids"])
            received = set(trade["team2_outgoing_player_ids"])
            if not sent <= by_id.keys() or not received <= by_id.keys():
                continue
            if any(by_id[player_id]["team_id"] != trade["team2_id"] for player_id in received):
                continue
            if len(own)-len(sent)+len(received) >= 15:
                continue
            next_roster = [
                *[row for row in profiles if row["team_id"] is None or (row["team_id"] == team_id and row["id"] not in sent)],
                *[{**by_id[player_id], "team_id": team_id} for player_id in sorted(received)],
            ]
            offers = _signing_options(next_roster, team_id, season, cap, remaining, priorities, games)
            if not offers:
                continue
            signing = next((offer for offer in offers if offer["payload"]["player_id"] not in used_signings), offers[0])
            draft = signing["payload"]
            after_signing = [*after_trade, _new_contract(season, by_id[draft["player_id"]]["position"], draft["salary"])]
            plan = {
                "title": trade_plan["title"], "subtitle": signing["title"] + " · " + signing["subtitle"],
                "opponent": trade_plan["subtitle"],
                "value": trade_plan["value"], "court_options": trade_plan["court_options"],
                "profiles": [*trade_plan["profiles"], *signing["profiles"]],
                "rating": rate_roster_change(
                    own, [*[by_id[value] for value in sorted(received)],by_id[draft["player_id"]]],
                    [by_id[value] for value in sorted(sent)], priorities, games,
                    salary_change=_projection(after_signing,year)["payroll"]-_projection(baseline,year)["payroll"],
                    projected_payroll=_projection(after_signing,year)["payroll"],
                    cap_amount=int(cap["cap_amount"]),tax_line=int(cap["luxury_tax_line"]),
                ),
                "signing_notes": signing["notes"],
                "budget": _budget(cursor, season, baseline, after_signing, after_trade),
                "payload": {"trade": trade, "followup": {"player_id": draft["player_id"], "salary": draft["salary"]}},
                "model_fact": trade_plan["model_fact"] + "后续自由市场：" + signing["model_fact"]
                              + ("交易后仍有税线余量。" if remaining > draft["salary"] else "签约将用尽税线余量。"),
            }
            if draft["player_id"] in used_signings:
                previously_used.append(plan)
            else:
                used_signings.add(draft["player_id"])
                candidates.append(plan)
            rival_count[trade["team2_id"]] += 1
            if len(candidates) >= SHORTLIST_LIMIT:
                break
    return _recommend("roster_packages", (candidates + previously_used)[:SHORTLIST_LIMIT], games, priorities,
                      "目前找不到同时通过交易配平、签约预算和阵容限制的组合。")
