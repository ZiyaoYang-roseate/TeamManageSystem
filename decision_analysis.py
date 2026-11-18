from collections import Counter

from analysis_model import ACTIONS, configured_model, generate_content
from basketball_fit import METRICS, PRIORITIES, display_profile, per_36, player_profiles, reliable, team_priorities
from cba import validate_and_calculate_offer
from db import transaction
from trade import validate_trade
from validation import BusinessError


def _factor(key, title, fact, action, model_fact, recommendation=None):
    return {"id": key, "title": title, "fact": fact, "actions": [action],
            "model_fact": model_fact, "recommendation": recommendation}


def _result(kind, factors, checks, profiles, baseline):
    # The model ranks categories. Exact terms, identities, and rule details stay in the app.
    source, report = generate_content({"kind": kind, "evidence": factors, "warnings": []}, configured_model())
    by_id = {item["id"]: item for item in factors}
    return {
        "source": source,
        "checks": checks,
        "profiles": profiles,
        "metric_labels": METRICS,
        "team_sample": baseline[0],
        "priorities": [{**gap, "label": METRICS[gap["metric"]]} for gap in baseline[1]],
        "items": [
            {
                "title": by_id[item["evidence_id"]]["title"],
                "fact": by_id[item["evidence_id"]]["fact"],
                "action": by_id[item["evidence_id"]]["recommendation"] or ACTIONS[item["action_id"]],
            }
            for item in report["items"]
        ],
    }


def _fit_factor(key, baseline, candidates, comparison, reference):
    priorities = baseline[1]
    if not candidates or not all(reliable(player) for player in candidates):
        fact = "至少一名待评估球员不足 3 场或累计 60 分钟，暂不做能力补强判断。"
        recommendation = "先补看候选球员的完整比赛，记录其轮换角色和受压表现，再决定报价或筹码。"
        if priorities:
            gap = priorities[0]
            metric = gap["metric"]
            fact += (f" 本队优先关注{METRICS[metric]}，场均 {gap['ours']:.1f}，"
                     f"同期对手 {gap['opponents']:.1f}（{baseline[0]} 场）。")
            recommendation = f"先补查候选球员在该指标上的比赛证据。{PRIORITIES[metric][1]}"
        return _factor(key, "先补球员样本", fact, "sample", "候选球员样本不足。", recommendation)
    if not comparison or not all(reliable(player) for player in comparison):
        return _factor(key, "先核实对比基线", f"{reference}的样本不足，暂不量化能力差值。",
                       "sample", "缺少可靠的比较基线。")
    if not priorities:
        return _factor(key, "用预期轮换检验适配", "当前样本没有可用于排序的明显技术差距。",
                       "roster_depth", "没有明确的优先补强指标。", "按预定的轮换角色比较候选与原阵容的同类回合，再决定是否补强。")
    gap = priorities[0]
    metric = gap["metric"]
    new, old = per_36(candidates, metric), per_36(comparison, metric)
    gain = old - new if metric == "turnovers" else new - old
    title, drill = PRIORITIES[metric]
    if gain < 0.05:
        title = f"方案对{METRICS[metric]}短板的帮助有限"
        drill = f"保留能承担{METRICS[metric]}职责的现有球员，或对比另一套候选方案。"
    return _factor(
        key, title,
        f"本队场均{METRICS[metric]} {gap['ours']:.1f}，同期对手 {gap['opponents']:.1f}（{baseline[0]} 场）。"
        f"候选方案每 36 分钟 {new:.1f}，{reference} {old:.1f}。",
        "roster_depth", "候选有望补充本队优先需求。" if gain >= 0.05 else "候选样本对本队优先需求帮助有限。",
        drill + "比较基于过往出场，后续用本队实际轮换验证。",
    )


def analyze_signing(team_id, target_id, terms):
    with transaction() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT p.id, p.name, p.team_id, p.position
            FROM free_agency_targets f JOIN players p ON p.id = f.player_id
            WHERE f.id = %s AND f.team_id = %s
            """,
            (target_id, team_id),
        )
        player = cursor.fetchone()
        if not player:
            raise BusinessError("本队招募目标不存在", 404)
        if player["team_id"] is not None:
            raise BusinessError("球员已不在自由市场", 409)
        rules = validate_and_calculate_offer(team_id, player["id"], terms, cursor=cursor)
        cursor.execute("SELECT id, name, position FROM players WHERE team_id = %s AND archived_at IS NULL ORDER BY id", (team_id,))
        roster = cursor.fetchall()
        profiles = player_profiles(cursor, [player, *roster], terms["season"])
        candidate, peers = profiles[0], [row for row in profiles[1:] if row["position"] == player["position"]]
        baseline = team_priorities(cursor, team_id, terms["season"])
        cursor.execute("SELECT luxury_tax_line FROM salary_caps WHERE season = %s", (terms["season"],))
        cap = cursor.fetchone()
    position = player["position"]
    current_depth = len(peers)
    checks = [*rules["errors"], *rules["warnings"]]
    if len(roster) >= 15:
        checks.append("正式签约前需腾出阵容名额。")
    if cap and rules.get("projected_payroll", 0) > cap["luxury_tax_line"]:
        checks.append("预计超过奢侈税线，当前系统的正式签约规则会拦截。")
    term = f"{terms['years']} 年，首年报价 ${terms['year1_salary']:,}。"
    if "projected_payroll" in rules:
        term += f" 按该报价计算，球队预计总薪资 ${rules['projected_payroll']:,}。"
    factors = [
        _fit_factor("fit", baseline, [candidate], [peer for peer in peers if reliable(peer)], "本队同位置有足量样本的球员"),
        _factor("depth", "同位置阵容深度", f"{player['name']}登记位置为 {position}，本队该位置将从 {current_depth} 人增至 {current_depth + 1} 人。",
                "roster_depth", "该球员可以补充人数较少的位置。" if current_depth < 2 else "该位置当前已有多名球员。",
                "优先考察担任该位置的第二轮换。" if current_depth < 2 else "先明确候选能替代哪位轮换球员的职责，再决定报价。"),
        _factor("offer", "报价与薪资", term, "offer_terms",
                "当前报价存在需处理的规则或预算提示。" if checks else "当前报价有可核对的合同与预算数据。"),
    ]
    return _result("signing", factors, checks, [
        display_profile(candidate, "拟签约"), *(display_profile(peer, "本队同位置") for peer in peers),
    ], baseline)


def analyze_trade(payload):
    team_id = payload["team1_id"]
    with transaction() as connection, connection.cursor() as cursor:
        validation = validate_trade(payload, cursor)
        cursor.execute("SELECT id, name, position FROM players WHERE team_id = %s AND archived_at IS NULL ORDER BY id", (team_id,))
        roster = cursor.fetchall()
        incoming_ids = payload["team2_outgoing_player_ids"]
        incoming = []
        if incoming_ids and payload["team2_id"] != team_id:
            placeholders = ", ".join(["%s"] * len(incoming_ids))
            cursor.execute(
                f"SELECT id, name, position FROM players WHERE team_id = %s AND archived_at IS NULL AND id IN ({placeholders}) ORDER BY id",
                (payload["team2_id"], *incoming_ids),
            )
            incoming = cursor.fetchall()
        outgoing_ids = set(payload["team1_outgoing_player_ids"])
        outgoing = [player for player in roster if player["id"] in outgoing_ids]
        outgoing = player_profiles(cursor, outgoing, payload["season"])
        incoming = player_profiles(cursor, incoming, payload["season"])
        baseline = team_priorities(cursor, team_id, payload["season"])
    valid = validation["is_valid"]
    before = Counter(player["position"] for player in roster)
    after = Counter(player["position"] for player in roster if player["id"] not in outgoing_ids)
    after.update(player["position"] for player in incoming)
    changes = "、".join(f"{position} {before[position]} → {after[position]} 人"
                       for position in sorted(before.keys() | after.keys()) if before[position] != after[position])
    payroll = validation["team1_summary"]
    uncovered = [position for position in before if after[position] == 0]
    factors = [
        _fit_factor("fit", baseline, incoming, outgoing, "换出球员"),
        _factor("depth", "本队位置变化", changes or "按登记位置计算，阵容人数分布没有变化。",
                "roster_depth", "部分位置人数下降。" if any(after[p] < before[p] for p in before) else "没有位置人数下降。",
                f"发送前需要为{'、'.join(uncovered)}补位，或从送出名单中保留该位置球员。" if uncovered else "用预期首发和第二阵容各试排一次，核对能否覆盖受影响的位置。"),
        _factor("budget", "本队薪资影响",
                f"拟换出 ${payroll['outgoing']:,}，换入 ${payroll['incoming']:,}，模拟后薪资空间 ${payroll['cap_space_after']:,}。",
                "trade_budget", "交易模拟改变本队的薪资空间。"),
    ]
    if not valid:
        factors.insert(0, _factor("rules", "交易需调整", "当前方案未通过系统交易校验。", "trade_rules", "存在交易规则阻碍。"))
    return _result("trade", factors, validation["reasons"], [
        *(display_profile(player, "拟换入") for player in incoming),
        *(display_profile(player, "拟换出") for player in outgoing),
    ], baseline)
