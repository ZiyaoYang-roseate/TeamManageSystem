from collections import Counter
from decimal import Decimal, ROUND_HALF_UP

from flask import abort

from basketball_fit import METRICS, team_priorities
from db import transaction
from draft_profiles import external_profile
from roster import contract_year_salaries, validate_signing_terms
from trade import validate_trade
from validation import BusinessError, bounded_float, bounded_int, choice, season_value


def _contracts(cursor, team_id):
    cursor.execute(
        """
        SELECT p.id AS player_id, p.name, p.position, c.start_year, c.end_year,
               c.annual_salary, c.annual_increase_pct, c.incentives, c.team_option, c.player_option
        FROM contracts c JOIN players p ON p.id = c.player_id
        WHERE c.team_id = %s AND c.status = 'Active'
        ORDER BY c.end_year, p.name, p.id
        """,
        (team_id,),
    )
    return cursor.fetchall()


def _projection(contracts, year):
    payroll = 0
    option_cost = 0
    depth = Counter()
    for row in contracts:
        if not row["start_year"] <= year <= row["end_year"]:
            continue
        index = year - row["start_year"]
        cost = contract_year_salaries(row["annual_salary"], index + 1, row["annual_increase_pct"])[index]
        cost += row["incentives"]
        payroll += cost
        depth[row["position"]] += 1
        if year == row["end_year"] and (row["team_option"] or row["player_option"]):
            option_cost += cost
    return {"payroll": payroll, "without_options": payroll - option_cost, "depth": dict(depth)}


def _budget(cursor, season, base, proposed=None, alternate=None, years=3):
    start = int(season.split("-", 1)[0])
    result = []
    for year in range(start, start + years):
        label = f"{year}-{(year + 1) % 100:02d}"
        cursor.execute("SELECT cap_amount, luxury_tax_line FROM salary_caps WHERE season = %s", (label,))
        cap = cursor.fetchone()
        current = _projection(base, year)
        result.append({
            "season": label, "cap": cap, "current": current,
            "projected": _projection(proposed, year) if proposed is not None else None,
            "alternate": _projection(alternate, year) if alternate is not None else None,
        })
    return result


def salary_plan(team_id, season):
    with transaction() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT name FROM teams WHERE id = %s", (team_id,))
        team = cursor.fetchone()
        if team is None:
            abort(404)
        contracts = _contracts(cursor, team_id)
        return team, _budget(cursor, season, contracts), contracts


def signing_preview(team_id, payload):
    season = season_value(payload.get("season"))
    target_id = bounded_int(payload.get("target_id"), "招募目标")
    years = bounded_int(payload.get("years"), "合同年限", 1, 5)
    salary = bounded_int(payload.get("year1_salary"), "首年薪资")
    increase = Decimal(str(bounded_float(payload.get("annual_increase_pct", 0), "年涨幅", 0, 0.08)))
    increase = increase.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    exception = choice(payload.get("exception_type", "Standard"), "合同特例", ("Standard", "Full MLE", "BAE"))
    team_option = payload.get("team_option", False)
    player_option = payload.get("player_option", False)
    if not isinstance(team_option, bool) or not isinstance(player_option, bool) or (team_option and player_option):
        raise BusinessError("请选择一种有效的合同选项")
    with transaction() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT p.id, p.name, p.position, p.age, p.team_id
            FROM free_agency_targets t JOIN players p ON p.id = t.player_id
            WHERE t.team_id = %s AND t.id = %s
            """,
            (team_id, target_id),
        )
        player = cursor.fetchone()
        if player is None:
            raise BusinessError("招募目标不存在", 404)
        if player["team_id"] is not None:
            raise BusinessError("球员已不在自由市场", 409)
        start = int(season.split("-", 1)[0])
        contract = {
            "position": player["position"], "start_year": start, "end_year": start + years - 1,
            "annual_salary": salary, "annual_increase_pct": increase, "incentives": 0,
            "team_option": team_option, "player_option": player_option,
        }
        base = _contracts(cursor, team_id)
        proposed = [*base, contract]
        alternate = [*base, {**contract, "end_year": start, "team_option": False, "player_option": False}] if years > 1 else None
        reasons = []
        try:
            validate_signing_terms(cursor, team_id, player, salary, years, season, increase, exception)
        except BusinessError as exc:
            reasons.append(str(exc))
        return {
            "label": f"签约 {player['name']} · {years} 年", "reasons": reasons,
            "years": _budget(cursor, season, base, proposed, alternate, years=max(3, years)),
        }


def trade_preview(team_id, payload):
    with transaction() as connection, connection.cursor() as cursor:
        result = validate_trade(payload, cursor)
        base = _contracts(cursor, team_id)
        # A rejected selection may contain player IDs that do not belong to the named team.
        # Show the validator's reasons and keep that selection out of the projection.
        proposed = None
        if result["is_valid"]:
            outgoing = set(payload["team1_outgoing_player_ids"])
            incoming = set(payload["team2_outgoing_player_ids"])
            proposed = [row for row in base if row["player_id"] not in outgoing]
            proposed.extend(row for row in _contracts(cursor, payload["team2_id"]) if row["player_id"] in incoming)
        return {
            "label": "交易方案", "reasons": result["reasons"],
            "years": _budget(cursor, payload["season"], base, proposed),
        }


def draft_workspace(team_id, season, selected_year=None):
    with transaction() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT draft_year FROM draft_prospects
            UNION SELECT CAST(season AS UNSIGNED) FROM draft_picks WHERE team_id = %s
            ORDER BY draft_year DESC
            """,
            (team_id,),
        )
        years = [row["draft_year"] for row in cursor.fetchall()]
        default_year = int(season.split("-", 1)[0]) + 1
        year = selected_year or (default_year if default_year in years or not years else years[-1])
        cursor.execute(
            """
            SELECT p.*, b.priority, b.role_note, b.notes
            FROM draft_prospects p LEFT JOIN draft_board b ON b.prospect_id = p.id AND b.team_id = %s
            WHERE p.draft_year = %s
            ORDER BY b.priority IS NULL, b.priority, p.name, p.id
            """,
            (team_id, year),
        )
        prospects = cursor.fetchall()
        rank = 0
        for prospect in prospects:
            prospect["profile"] = external_profile(prospect)
            if prospect["priority"] is not None:
                rank += 1
                prospect["priority"] = rank
        cursor.execute(
            "SELECT round, status, protection FROM draft_picks WHERE team_id = %s AND season = %s ORDER BY round, id",
            (team_id, str(year)),
        )
        picks = cursor.fetchall()
        reference = _draft_order(cursor, team_id, year)
        games, priorities = team_priorities(cursor, team_id, season)
        cursor.execute(
            """
            SELECT p.position, COUNT(*) AS total FROM contracts c
            JOIN players p ON p.id = c.player_id
            WHERE c.team_id = %s AND c.status = 'Active' AND c.end_year = %s
            GROUP BY p.position ORDER BY p.position
            """,
            (team_id, year - 1),
        )
        return {
            "year": year, "years": sorted(set(years) | {year}, reverse=True),
            "prospects": prospects, "picks": picks, "games": games,
            "next_rank": rank + 1, "draft_order": reference,
            "priorities": [METRICS[item["metric"]] for item in priorities],
            "needs": [item["metric"] for item in priorities], "expiring": cursor.fetchall(),
        }

def update_draft_board(team_id, year, prospect_id, priority=None, role="", notes=""):
    from roster import lock_teams

    with transaction() as connection, connection.cursor() as cursor:
        lock_teams(cursor, team_id)
        cursor.execute("SELECT id FROM draft_prospects WHERE id = %s AND draft_year = %s", (prospect_id, year))
        if cursor.fetchone() is None:
            abort(404)
        cursor.execute(
            """
            SELECT b.prospect_id FROM draft_board b JOIN draft_prospects p ON p.id = b.prospect_id
            WHERE b.team_id = %s AND p.draft_year = %s AND b.prospect_id <> %s
            ORDER BY b.priority, p.name, p.id
            """,
            (team_id, year, prospect_id),
        )
        order = [item["prospect_id"] for item in cursor.fetchall()]
        if priority is None:
            cursor.execute("DELETE FROM draft_board WHERE team_id = %s AND prospect_id = %s", (team_id, prospect_id))
        else:
            insert_at = min(priority - 1, len(order))
            order.insert(insert_at, prospect_id)
            cursor.execute(
                """
                INSERT INTO draft_board (team_id, prospect_id, priority, role_note, notes)
                VALUES (%s, %s, %s, %s, %s) AS entry
                ON DUPLICATE KEY UPDATE priority=entry.priority, role_note=entry.role_note, notes=entry.notes
                """,
                (team_id, prospect_id, insert_at + 1, role, notes),
            )
        if order:
            cursor.executemany(
                "UPDATE draft_board SET priority = %s WHERE team_id = %s AND prospect_id = %s AND priority <> %s",
                [(rank, team_id, value, rank) for rank, value in enumerate(order, 1)],
            )


def _draft_order(cursor, team_id, year):
    start = year - 1
    season = f"{start}-{year % 100:02d}"
    cursor.execute(
        """
        WITH results AS (
            SELECT home_team_id AS team_id, home_score > away_score AS won FROM games WHERE season=%s AND status='finished'
            UNION ALL
            SELECT away_team_id, away_score > home_score FROM games WHERE season=%s AND status='finished'
        ), win_rates AS (
            SELECT t.id, COUNT(r.team_id) AS played, AVG(r.won) AS win_pct
            FROM teams t LEFT JOIN results r ON r.team_id=t.id GROUP BY t.id
        ), ordering AS (
            SELECT id, played, ROW_NUMBER() OVER (ORDER BY win_pct IS NULL, win_pct, id) AS placement,
                   COUNT(*) OVER () AS teams_count
            FROM win_rates
        )
        SELECT placement, teams_count, played FROM ordering WHERE id = %s
        """,
        (season, season, team_id),
    )
    row = cursor.fetchone()
    return row if row and row["played"] else None


def draft_comparison(workspace, selected):
    from analysis_model import ACTIONS, configured_model, generate_content

    metrics = {
        "assists": ("每36分钟助攻", "draft_creation"),
        "rebounds": ("每36分钟篮板", "draft_rebounding"),
        "threes": ("每36分钟三分命中", "draft_spacing"),
        "turnovers": ("每36分钟失误", "draft_pressure"),
    }
    metric = next((item for item in workspace["needs"] if item in metrics), None)
    axis, action = metrics.get(metric, ("", "draft_sample"))
    prospects = [row for row in workspace["prospects"] if row["id"] in selected]
    if len(prospects) != len(selected) or not 2 <= len(prospects) <= 3:
        raise BusinessError("请选择同届的 2 至 3 名候选人")
    comparisons = []
    evidence = []
    for index, row in enumerate(prospects, 1):
        profile = row["profile"]
        fields = profile["fields"]
        minutes = int(row["minutes"])
        supported = profile["external"] and row["games"] >= 5 and row["games"] * minutes >= 100
        rate = round(float(row[metric]) * 36 / minutes, 1) if metric and minutes and profile["external"] else None
        if not profile["external"]:
            readout = "先补齐外部赛事出处、样本及球探记录。"
        elif not supported:
            readout = "样本不足，建议先补看不同对手的比赛。"
        elif metric == "turnovers":
            readout = "在相同持球任务下验证失误。低失误也可能来自较少的触球。"
        elif metric:
            readout = f"用试训检查能否补上本队的{METRICS[metric]}需求。外部赛场数据只用于初筛。"
        else:
            readout = "先核对预计上场角色和本队轮换位置。"
        comparison = {
            "name": row["name"], "position": row["position"], "source": profile["source"],
            "games": row["games"], "minutes": minutes, "rate": rate, "supported": supported,
            "fields": fields, "readout": readout,
        }
        comparisons.append(comparison)
        if supported:
            evidence.append({
                "id": f"candidate{index}", "actions": list(dict.fromkeys((action, "draft_sample"))),
                "fact": f"外部样本位置 {row['position']}，{row['games']} 场、每场 {minutes} 分钟。"
                        f"{axis or '无指定量化方向'}：{rate if rate is not None else '无'}。只推荐到队试训的观察重点。",
            })
    source, content = generate_content(
        {"kind": "draft", "warnings": ["外部比赛环境不同。禁止将外部数据当成加盟后的比赛预测。"], "evidence": evidence},
        configured_model(),
    )
    for picked in content["items"]:
        index = int(picked["evidence_id"].removeprefix("candidate")) - 1
        comparisons[index]["focus"] = ACTIONS[picked["action_id"]]
    return {"source": source, "axis": axis, "candidates": comparisons, "selected": selected}


def draft_recommendations(workspace):
    from analysis_model import ACTIONS, configured_model, generate_content

    axes = {"assists": (4, "draft_creation"), "rebounds": (7, "draft_rebounding"),
            "threes": (2, "draft_spacing"), "turnovers": (3, "draft_pressure")}
    expiring = {row["position"]: row["total"] for row in workspace["expiring"]}
    ranked = []
    for row in workspace["prospects"]:
        profile = row["profile"]
        if not profile["external"] or not row["games"] or not row["minutes"]:
            continue
        rates = {key: round(float(row[key])*36/float(row["minutes"]), 1) for key in axes}
        supported = row["games"] >= 5 and row["games"]*row["minutes"] >= 100
        score = .5*bool(expiring.get(row["position"])) + .5*supported
        needs = [key for key in workspace["needs"] if key in axes]
        for index, key in enumerate(needs):
            scale = axes[key][0]
            # External rates rank prospects, never predict league production.
            score += (1/(1+rates[key]/scale) if key=="turnovers" else rates[key]/scale)/(index+1)
        if not needs:
            score += sum(rates[key]/axes[key][0] for key in ("assists","rebounds","threes"))/3
        ranked.append((score,row,rates,supported))
    ranked.sort(key=lambda item:(-item[0],item[1]["id"]))
    shortlist = ranked[:8]
    evidence=[]
    for _,row,rates,supported in shortlist:
        actions=list(dict.fromkeys([axes[key][1] for key in workspace["needs"] if key in axes]+["draft_sample"]))
        evidence.append({"id":f"prospect{row['id']}","actions":actions,
            "fact":f"候选 {row['name']}，{row['position']}，外部出处 {row['profile']['source']}，{row['games']}场、场均{row['minutes']}分钟。"
            f"每36分钟初筛 {rates}。本队关注 {workspace['priorities']}，到期位置 {expiring}。"
            f"技术特点、角色、风险、试训：{row['profile']['fields']}。"
            + ("有基本样本。" if supported else "样本有限，先补看录像。")})
    if not evidence:
        return {"source":"rules","candidates":[],"message":"暂无具备外部出处和出场样本的候选人，请先补齐候选资料。"}
    source,content=generate_content({"kind":"draft","warnings":[
        "从候选中优先推荐能补上本队短板或到期轮换的人选，兼顾成长风险和试训。",
        "外部比赛水平不同，不预测加盟后的统计，不声称一定能在本队签位选到。",
    ],"evidence":evidence},configured_model())
    selected={item["evidence_id"]:item["action_id"] for item in content["items"]}
    by_evidence={f"prospect{r[1]['id']}":r for r in shortlist}
    order=list(dict.fromkeys([*selected,*by_evidence]))[:3]
    result=[]
    for key in order:
        _,row,rates,supported=by_evidence[key]
        result.append({**row,"rates":rates,"supported":supported,
            "reason":f"本队优先关注{'、'.join(workspace['priorities']) or '轮换储备'}。"
                     +(f"{row['position']} 有 {expiring[row['position']]} 份合同即将到期，可提前考察接续人选。" if row['position'] in expiring else "结合现有轮换安排发展时间。"),
            "focus":ACTIONS.get(selected.get(key),ACTIONS["draft_sample"])})
    return {"source":source,"candidates":result,"message":"建议顺序用于考察优先级，不代表正式选秀顺位。"}
