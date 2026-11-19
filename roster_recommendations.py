from collections import Counter, defaultdict

from analysis_model import configured_model, generate_content
from basketball_fit import METRICS, display_profile, per_36, player_profiles, reliable, team_priorities
from db import transaction
from roster import max_contract_salary, season_salary_sql, team_payroll
from roster_rating import rate_roster_change
from trade_packages import ranked_packages, package_plan
from trade import decode_proposal_side, validate_trade


PLAN_LIMIT = 3
SHORTLIST_LIMIT = 8
# Use the same starting figure as the quote sandbox for illustrative offers.
TRIAL_SALARY = 1_200_000
METRIC_SCALE = {"points": 12, "rebounds": 5, "assists": 4, "threes": 2, "turnovers": 3}
COURT_ROLES = {
    "assists": (
        "trade_creation", "为第二阵容增加一个进攻发起点",
        "主力持球手休息或被施压时，让他接过部分组织职责，通过转移球和给空切队友做球，帮助更多队友参与进攻。",
        "在接下来三场对比这个轮换时段的助攻、失误和队友接球出手机会。",
    ),
    "rebounds": (
        "trade_rebounding", "加强轮换阵容的篮板保护",
        "让他在轮换时段明确承担弱侧卡位和收篮板任务，帮助球队结束防守回合，减少补防后被对手拿到二次进攻的机会。",
        "记录其在场时对手的二次进攻次数，并回看是否因冲抢影响退防。",
    ),
    "threes": (
        "trade_spacing", "为持球核心提供外线接应",
        "当主攻手突破吸引协防时，让他保持外线接应站位，试用接球出手或二次转移球，为队友腾出突破空间。",
        "记录出手位置、接球出手命中率和队友的突破空间。三分命中数本身无法说明投篮效率。",
    ),
    "points": (
        "trade_scoring", "给第二阵容增加一个进攻终结选择",
        "主力得分手休息时给他安排一部分终结任务，先通过录像确定他更适合接球、空切还是持球出手。",
        "对比这段轮换的得分、出手质量和效率，观察是否减少连续无得分回合。",
    ),
    "turnovers": (
        "trade_possession", "让轮换阵容多一个可试用的安全接应点",
        "先让他承担明确的接应和转移球职责。当持球核心遇到夹击时，尝试增加一个可出球的选项。",
        "在相近的持球任务下比较失误和出球成功率。每 36 分钟失误较少也可能来自较少的持球机会。",
    ),
}
TRADE_STYLE_EXAMPLES = [
    "NBA.com 的勇士报道从库里休息的时段讲保罗的职责，也解释两人同场时如何让库里更多打无球。",
    "骑士管理层谈亨特时，将较低的持球需求与现有阵容适配联系起来，并说明他能承担怎样的防守对位。",
]


def _money(amount):
    return f"${int(amount):,}"


def _rates(player):
    return {metric: per_36([player], metric) for metric in METRIC_SCALE} if reliable(player) else {}


def _team_needs(priorities):
    return [
        f"本队场均{METRICS[row['metric']]} {row['ours']:.1f}，同期对手 {row['opponents']:.1f}。"
        for row in priorities[:3]
    ]


def _recommend(kind, candidates, games, priorities, empty_message):
    if not candidates:
        return {"source": "rules", "plans": [], "team_sample": games,
                "needs": _team_needs(priorities), "message": empty_message}
    options = [
        {"id": f"option{index}", "fact": plan["model_fact"] + (
             f"本队综合评级 {plan['rating']['grade']}，{plan['rating']['summary']}" if plan.get("rating") else ""),
         "actions": list(plan.get("court_options") or ["roster_depth"])}
        for index, plan in enumerate(candidates)
    ]
    model = configured_model() if len(options) > 1 or len(options[0]["actions"]) > 1 else None
    source, output = generate_content({
        "kind": kind,
        "warnings": [
            "候选已通过系统预算和阵容约束。优先选择有样本支持、能填补本队短板且代价合理的方案。",
            *(
                ["对每套交易选择最能帮助未来比赛的轮换用法。从允许的行动中选出与短板最契合的角色。"]
                if kind == "trade_options" else []
            ),
        ],
        "evidence": options,
        "style_examples": TRADE_STYLE_EXAMPLES if kind == "trade_options" else [],
    }, model)
    positions = {option["id"]: index for index, option in enumerate(options)}
    chosen = {positions[item["evidence_id"]]: item["action_id"] for item in output["items"]}
    ordered = list(dict.fromkeys([*chosen, *range(len(candidates))]))
    if kind == "trade_options":
        # Prefer genuinely different packages where there are enough viable choices.
        distinct = []
        used = set()
        for index in ordered:
            outgoing = (len(candidates[index]["payload"]["team1_outgoing_player_ids"]), len(candidates[index]["payload"].get("team2_outgoing_player_ids", [None])))
            if outgoing not in used:
                distinct.append(index)
                used.add(outgoing)
        ordered = list(dict.fromkeys([*distinct, *ordered]))
    ordered = ordered[:PLAN_LIMIT]
    plans = []
    for index in ordered:
        candidate = candidates[index]
        plan = {key: value for key, value in candidate.items() if key not in {"model_fact", "court_options"}}
        if index in chosen and candidate.get("court_options"):
            plan["value"] = {**plan["value"], **candidate["court_options"][chosen[index]]}
        plans.append(plan)
    return {
        "source": source, "team_sample": games, "needs": _team_needs(priorities),
        "plans": plans,
    }


def trade_recommendations(team_id, season):
    candidates, games, priorities, message = trade_candidates(team_id, season)
    return _recommend("trade_options", candidates, games, priorities, message)


def trade_candidates(team_id, season, limit=SHORTLIST_LIMIT, reserve_salary=0):
    year = int(season.split("-", 1)[0])
    with transaction() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT cap_amount, luxury_tax_line FROM salary_caps WHERE season = %s", (season,))
        cap = cursor.fetchone()
        if not cap:
            return [], 0, [], "该赛季缺少工资帽数据。"
        cap_amount = int(cap["cap_amount"])
        cursor.execute(
            f"""
            SELECT p.id, p.name, p.position, p.team_id, t.name AS team_name,
                   COALESCE({season_salary_sql()} + c.incentives, 0) AS salary
            FROM players p JOIN teams t ON t.id = p.team_id
            JOIN users u ON u.team_id = t.id AND u.role = 'management'
            LEFT JOIN contracts c ON c.active_player_id = p.id AND c.team_id = p.team_id
                AND c.start_year <= %s AND c.end_year >= %s
            WHERE p.archived_at IS NULL AND t.archived_at IS NULL
            ORDER BY p.id
            """,
            (year, year, year),
        )
        players = player_profiles(cursor, cursor.fetchall(), season)
        cursor.execute(
            f"""
            SELECT team_id, SUM({season_salary_sql()} + incentives) AS payroll FROM contracts c
            WHERE status = 'Active' AND start_year <= %s AND end_year >= %s GROUP BY team_id
            """,
            (year, year, year),
        )
        payroll = {row["team_id"]: int(row["payroll"]) for row in cursor.fetchall()}
        games, priorities = team_priorities(cursor, team_id, season)
        cursor.execute(
            """
            SELECT receiver_team_id, proposer_outgoing, receiver_outgoing FROM trade_proposals
            WHERE proposer_team_id = %s AND season = %s AND status = 'pending'
            """,
            (team_id, season),
        )
        pending = set()
        for row in cursor.fetchall():
            sent, _ = decode_proposal_side(row["proposer_outgoing"])
            received, _ = decode_proposal_side(row["receiver_outgoing"])
            pending.add((row["receiver_team_id"], tuple(sorted(sent)), tuple(sorted(received))))
        rosters = defaultdict(list)
        for player in players:
            rosters[player["team_id"]].append(player)
        ranked = ranked_packages(team_id, rosters, payroll, cap_amount, priorities, pending, bool(reserve_salary))
        own_payroll = payroll.get(team_id, 0)
        candidates = []
        used = set()
        for _, outgoing, incoming, metric in ranked:
            rival = incoming["players"][0]["team_id"]
            key = (rival, outgoing["ids"], incoming["ids"])
            if key in used:
                continue
            payload = {
                "team1_id": team_id, "team2_id": rival, "season": season,
                "team1_outgoing_player_ids": list(outgoing["ids"]),
                "team2_outgoing_player_ids": list(incoming["ids"]),
            }
            validation = validate_trade(payload, cursor)
            if not validation["is_valid"]:
                continue
            salary = validation["team1_summary"]
            if reserve_salary and own_payroll + salary["incoming"] - salary["outgoing"] + reserve_salary > cap["luxury_tax_line"]:
                continue
            used.add(key)
            if len(outgoing["players"]) == len(incoming["players"]) == 1:
                candidates.append(_trade_plan(payload, outgoing["players"][0], incoming["players"][0], metric,
                    salary, Counter(p["position"] for p in rosters[team_id]), priorities,
                    own_payroll, int(cap["luxury_tax_line"])))
            else:
                candidates.append(package_plan(payload, outgoing, incoming, metric, salary,
                    rosters[team_id], rosters[rival], priorities, own_payroll, int(cap["luxury_tax_line"])))
            candidates[-1]["rating"] = rate_roster_change(
                rosters[team_id], list(incoming["players"]), list(outgoing["players"]), priorities, games,
                salary_change=salary["incoming"]-salary["outgoing"],
                projected_payroll=own_payroll+salary["incoming"]-salary["outgoing"],
                cap_amount=cap_amount,tax_line=int(cap["luxury_tax_line"]),
            )
            if len(candidates) >= limit:
                break
    return candidates, games, priorities, "当前没有同时满足薪资配平、双方位置覆盖和数据适配条件的交易方案。"


def _cap_position(amount):
    return f"可用 {_money(amount)}" if amount >= 0 else f"超帽 {_money(-amount)}"


def _court_options(incoming, outgoing, preferred, priorities):
    if not preferred:
        return {"roster_depth": {
            "summary": f"增加一名 {incoming['position']} 轮换备选",
            "usage": "先安排有限的轮换时间，观察球员能否承担该位置的接应、防守和篮板职责。",
            "court": "两人的有效出场样本不足，现有数据只能支持位置补充。",
            "verify": "用录像和试训了解适合的职责，再决定是否增加上场时间。",
        }}, ["只有位置深度依据，缺少足量技术样本。"]

    before, after = _rates(outgoing), _rates(incoming)
    needs = {row["metric"]: row for row in priorities}
    options = {}
    facts = []
    for metric in dict.fromkeys([preferred, *needs, *COURT_ROLES]):
        direction = -1 if metric == "turnovers" else 1
        gain = (after[metric] - before[metric]) * direction
        if metric != preferred and gain < max(0.25, METRIC_SCALE[metric] * 0.07):
            continue
        action, summary, usage, verify = COURT_ROLES[metric]
        label = METRICS[metric]
        court = f"历史每 36 分钟{label}：换入 {after[metric]:.1f}，换出 {before[metric]:.1f}。"
        if metric in needs:
            gap = needs[metric]
            court += f"这是本队的短板，本队场均 {gap['ours']:.1f}，同期对手 {gap['opponents']:.1f}。"
        if metric == "assists" and after["turnovers"] - before["turnovers"] >= 0.5:
            usage += "由于历史失误也更高，先从简单的接应和二次发起做起，再逐步增加持球任务。"
        options[action] = {"summary": summary, "usage": usage, "court": court, "verify": verify}
        facts.append(f"{action}：历史数据支持{label}{'减少' if direction < 0 else '增加'} {gain:.1f}。"
                     + ("对应当前球队短板。" if metric in needs else ""))
        if len(options) == 3:
            break
    return options, facts


def _trade_plan(payload, outgoing, incoming, metric, salary, depth, priorities, payroll, tax_line):
    out_position, in_position = outgoing["position"], incoming["position"]
    if in_position == out_position:
        rotation = f"同为 {in_position}，该位置仍有 {depth[in_position]} 人。可沿用原位置的轮换名额，再验证两人的职责差异。"
    else:
        rotation = (f"{in_position} 从 {depth[in_position]} 人增至 {depth[in_position] + 1} 人，"
                    f"{out_position} 从 {depth[out_position]} 人减至 {depth[out_position] - 1} 人。")
        if depth[out_position] == 2:
            rotation += f"{out_position} 只剩一名登记球员，需提前安排替补。"

    change = salary["incoming"] - salary["outgoing"]
    new_payroll = payroll + change
    headroom = salary["cap_space_after"]
    if change > 0:
        cost = f"赛季总薪资增加 {_money(change)}"
    elif change < 0:
        cost = f"赛季总薪资节省 {_money(-change)}"
    else:
        cost = "赛季总薪资持平"
    if new_payroll > tax_line:
        tax_note = f"交易后仍超奢侈税线 {_money(new_payroll - tax_line)}。" if payroll > tax_line else f"交易后将超出奢侈税线 {_money(new_payroll - tax_line)}。"
    elif payroll > tax_line:
        tax_note = f"交易后回到奢侈税线以内，剩余 {_money(tax_line - new_payroll)}。"
    else:
        tax_note = f"交易后距奢侈税线还有 {_money(tax_line - new_payroll)}。"
    budget = (f"{cost}，由 {_money(payroll)} 变为 {_money(new_payroll)}。"
              f"工资帽空间由{_cap_position(headroom + change)}变为{_cap_position(headroom)}。{tax_note}")

    tradeoff = "至少一名球员的比赛样本不足。先核对两人的录像和轮换职责，再判断赛场收益。"
    options, role_facts = _court_options(incoming, outgoing, metric, priorities)
    if metric:
        before, after = _rates(outgoing), _rates(incoming)
        losses = []
        for key, scale in METRIC_SCALE.items():
            loss = (after[key] - before[key]) * (1 if key == "turnovers" else -1)
            if loss / scale >= 0.1:
                losses.append((loss / scale, key))
        losses.sort(reverse=True)
        if losses:
            comparison = "、".join(
                f"{METRICS[key]} {before[key]:.1f} → {after[key]:.1f}"
                for _, key in losses[:2]
            )
            tradeoff = f"换入球员历史每 36 分钟表现有取舍：{comparison}。结合预计上场时间检查第二阵容能否弥补。"
        else:
            tradeoff = "比较来自两人的历史出场。上场时间和角色变化可能影响实际收益，安排同类轮换回合复核。"
        if losses:
            role_facts.append("换入球员另有技术指标下降。")

    return {
        "title": f"送出 {outgoing['name']}，换入 {incoming['name']}",
        "subtitle": f"交易对手 · {incoming['team_name']}",
        "value": {
            **next(iter(options.values())), "rotation": rotation, "budget": budget, "tradeoff": tradeoff,
        },
        "court_options": options,
        "payload": payload,
        "profiles": [display_profile(outgoing, "送出"), display_profile(incoming, "换入")],
        "model_fact": "".join(role_facts) + ("薪资降低。" if change < 0 else "薪资增加。" if change > 0 else "薪资持平。"),
    }


def signing_recommendations(team_id, season):
    with transaction() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT cap_amount, luxury_tax_line FROM salary_caps WHERE season = %s", (season,))
        cap = cursor.fetchone()
        games, priorities = team_priorities(cursor, team_id, season)
        if not cap:
            return _recommend("signing_options", [], games, priorities, "该赛季缺少工资帽数据。")
        payroll = team_payroll(team_id, season, cursor)
        remaining = int(cap["luxury_tax_line"]) - payroll
        if remaining < TRIAL_SALARY:
            needed = _money(TRIAL_SALARY - remaining)
            return _recommend("signing_options", [], games, priorities,
                              f"当前预算不足，还需腾出 {needed} 才能提交建议起始报价。")
        cursor.execute("SELECT id, name, position, age, team_id FROM players WHERE archived_at IS NULL AND (team_id = %s OR team_id IS NULL) ORDER BY id", (team_id,))
        players = cursor.fetchall()
        own = [player for player in players if player["team_id"] == team_id]
        if len(own) >= 15:
            return _recommend("signing_options", [], games, priorities, "本队阵容已满 15 人，腾出名额后可生成签约方案。")
        profiles = player_profiles(cursor, players, season)
        candidates = _signing_options(profiles, team_id, season, cap, remaining, priorities, games)
    return _recommend("signing_options", candidates, games, priorities, "当前没有满足薪资约束的可签自由球员。")


def _signing_options(profiles, team_id, season, cap, remaining, priorities, games=0):
    roster = [player for player in profiles if player["team_id"] == team_id]
    depth = Counter(player["position"] for player in roster)
    metric = priorities[0]["metric"] if priorities else None
    ranked = []
    for player in profiles:
        if player["team_id"] is not None:
            continue
        position = player["position"]
        rates = _rates(player)
        comparison = [peer for peer in roster if peer["position"] == position and reliable(peer)]
        fit = 0.0
        notes = [f"登记位置 {position}，本队该位置 {depth[position]} → {depth[position] + 1} 人。"]
        if not rates:
            notes.append("缺少足量比赛样本。建议仅作低成本轮换考察，先核对球员录像和实际意愿。")
        elif metric and comparison:
            baseline = per_36(comparison, metric)
            fit = (rates[metric] - baseline) * (-1 if metric == "turnovers" else 1) / METRIC_SCALE[metric]
            notes.append(f"每 36 分钟{METRICS[metric]}：候选 {rates[metric]:.1f}，本队同位置 {baseline:.1f}。")
        elif metric:
            notes.append(f"本队优先关注{METRICS[metric]}，候选每 36 分钟 {rates[metric]:.1f}，尚无足量的本队同位置对照。")
        else:
            notes.append("已有球员比赛样本，可对照阵容角色与下方技术指标。")
        ceiling = min(max_contract_salary(cap["cap_amount"], player["age"]), remaining)
        if ceiling < TRIAL_SALARY:
            continue
        salary = min(ceiling, TRIAL_SALARY + (TRIAL_SALARY if rates and fit > 0.3 and remaining >= 4 * TRIAL_SALARY else 0))
        notes.append(f"试拟一年固定年薪 {_money(salary)}，签后距离奢侈税线 {_money(remaining - salary)}。")
        score = (0.5 if rates else 0) + max(-1.0, min(2.0, fit)) + 1 / (1 + depth[position])
        ranked.append((score, player["id"], {
            "title": f"签约 {player['name']}",
            "subtitle": f"{position} · 1 年 · {_money(salary)}",
            "notes": notes,
            "payload": {"player_id": player["id"], "salary": salary, "years": 1, "season": season},
            "profiles": [display_profile(player, "候选")],
            "rating": rate_roster_change(
                roster, [player], [], priorities, games,
                salary_change=salary,projected_payroll=int(cap["luxury_tax_line"])-int(remaining)+salary,
                cap_amount=int(cap["cap_amount"]),tax_line=int(cap["luxury_tax_line"]),
            ),
            "model_fact": f"位置现有 {depth[position]} 人；"
                          + ("有足量技术样本；" if rates else "缺少足量比赛样本，需先考察；")
                          + (f"优先指标优势 {fit:+.1f}（相对于同位置）。" if rates and metric and comparison else "以占用少量预算的试拟合同补充阵容。"),
        }))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return [plan for _, _, plan in ranked[:SHORTLIST_LIMIT]]
