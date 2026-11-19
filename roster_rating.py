"""Explainable, team-specific grades for proposed roster changes."""
from collections import Counter

from basketball_fit import METRICS, per_36, reliable


SCALES = {"points": 12, "rebounds": 5, "assists": 4, "threes": 2, "turnovers": 3}
BENEFITS = {
    "assists": "可试任第二进攻发起点，帮助队友接球和完成进攻",
    "rebounds": "可加强卡位和篮板保护，帮助球队结束防守回合",
    "threes": "可增加外线接应选择，为持球核心创造突破空间",
    "turnovers": "可尝试承担受压接应和转移球职责，降低球权处理风险",
    "points": "可增加轮换时段的终结选择，分担主力得分任务",
}
GRADE_LABELS = {"S": "显著补强", "A": "优先考虑", "B": "有补强价值",
                "C": "收益有限或待验证", "D": "补强价值偏低", "E": "不建议当前方案"}


def _clamp(value, low, high):
    return min(high, max(low, value))


def rate_roster_change(roster, incoming, outgoing, priorities, games, *, salary_change=0,
                       projected_payroll=0, cap_amount=0, tax_line=0, years=1, valid=True):
    """Score historical evidence for future roles, never projected box-score increments."""
    before = Counter(p["position"] for p in roster)
    after = before.copy()
    after.subtract(p["position"] for p in outgoing)
    after.update(p["position"] for p in incoming)
    # Signings compare to the existing players at the candidate's position.
    positions = {p["position"] for p in incoming}
    comparison = outgoing or [p for p in roster if p["position"] in positions and reliable(p)]
    supported = bool(incoming) and all(reliable(p) for p in incoming)
    comparable = supported and bool(comparison) and all(reliable(p) for p in comparison)
    limits = []
    court = 30.0
    court_reason = "暂缺足量的候选或同位置比较样本，无法确认具体能力提升。"
    if comparable:
        keys = list(dict.fromkeys(p["metric"] for p in priorities)) or list(SCALES)
        weights = {key: 1/(i+1) if priorities else 1 for i, key in enumerate(keys)}
        gains = {key: (per_36(incoming,key)-per_36(comparison,key))
                 * (-1 if key=="turnovers" else 1)/SCALES[key] for key in SCALES}
        gain = sum(_clamp(gains[key],-1,1)*weight for key,weight in weights.items())/sum(weights.values())
        court += (35 if priorities else 20)*gain
        # Filling one need must not hide losing another major rotation contribution.
        losses = [(key,value) for key,value in gains.items() if key not in keys and value < -.2]
        court -= sum(min(6, -value*6) for _,value in losses)
        best = max(keys, key=lambda key:gains[key])
        if gains[best] > .05:
            label = METRICS[best]
            old, new = per_36(comparison,best), per_36(incoming,best)
            court_reason = (f"历史每36分钟{label}：候选组 {new:.1f}，比较组 {old:.1f}。"
                            + (f"对应本队的{label}短板，" if priorities else "可作为轮换试用依据，")
                            + BENEFITS[best]+"。")
        else:
            court_reason = "候选组对当前优先需求的历史表现没有明显优势，需重新考虑其轮换职责。"
        if losses:
            court_reason += "同时需弥补"+"、".join(METRICS[key] for key,_ in losses)+"方面的轮换损失。"
    court = _clamp(court,0,60)

    rotation = 18.0
    uncovered = [pos for pos in before if after[pos] <= 0]
    depleted = [pos for pos in before if before[pos]>=2 and after[pos]==1]
    filled = [pos for pos in after if before[pos]<2 and after[pos]>before[pos]]
    rotation += sum(7 if before[pos]==0 else 4 for pos in filled)
    rotation -= 15*len(uncovered)+3*len(depleted)
    rotation -= 3*sum(before[pos]>=3 and after[pos]>before[pos] for pos in after)
    rotation -= max(0,len(outgoing)-len(incoming))
    rotation = _clamp(rotation,0,25)
    rotation_notes = []
    if filled:
        rotation_notes.append("补充 "+"、".join(f"{pos}（{before[pos]}→{after[pos]}人）" for pos in filled))
    if uncovered:
        rotation_notes.append("需要补回 "+"、".join(uncovered)+" 位置")
    if depleted:
        rotation_notes.append("、".join(depleted)+" 只剩一名登记球员")
    if not rotation_notes:
        rotation_notes.append("按登记位置核对轮换覆盖；仍需安排换入球员的上场时间")
    rotation_reason = "；".join(rotation_notes)+"。"

    # Payroll is a secondary factor: savings alone cannot produce a top grade.
    salary_change = float(salary_change)
    finance = 10.0
    if cap_amount:
        finance -= _clamp(salary_change/(float(cap_amount)*.05),-1,1)*5
    finance -= min(4,max(0,years-1))
    if tax_line and projected_payroll>tax_line:
        finance -= 5
    finance = _clamp(finance,0,15)
    finance_reason = (f"本赛季总薪资{'增加' if salary_change>0 else '减少' if salary_change<0 else '变动'} "
                      f"${abs(int(salary_change)):,}；承诺 {years} 年。")
    if tax_line:
        difference = float(tax_line)-float(projected_payroll)
        finance_reason += f"方案后{'距奢侈税线' if difference>=0 else '超奢侈税线'} ${abs(int(difference)):,}。"

    score = round(court+rotation+finance)
    confidence = "较高" if comparable and games>=3 else "有限"
    if not supported:
        limits.append("候选球员不足3场或累计60分钟，评级最高为C，先补充比赛证据。")
        score = min(score,59)
    elif not comparable or not priorities or games<3:
        limits.append("缺少明确的球队短板或可靠比较基线，评级最高为B。")
        score = min(score,79)
    if uncovered:
        limits.append("方案留下登记位置空缺，评级最高为D，需配套补位。")
        score = min(score,49)
    if not valid:
        limits.append("当前方案未通过规则或预算校验，调整后重新评估。")
        score = 0
    score = int(_clamp(score,0,100))
    grade = next((grade for threshold,grade in [(90,"S"),(80,"A"),(65,"B"),(50,"C"),(35,"D")] if score>=threshold),"E")
    return {
        "grade": grade,"score":score,"label":GRADE_LABELS[grade],"confidence":confidence,
        "summary":court_reason,
        "dimensions":[{"label":"赛场补强","score":round(court,1),"max":60,"reason":court_reason},
                      {"label":"轮换适配","score":round(rotation,1),"max":25,"reason":rotation_reason},
                      {"label":"薪资成本","score":round(finance,1),"max":15,"reason":finance_reason}],
        "limits":limits,"method":"赛场补强60% · 轮换适配25% · 薪资成本15%",
        "note":"评级仅针对本队当前方案，依据历史样本判断未来职责，不是胜率或加盟后统计预测。",
        "scale":"S≥90 · A≥80 · B≥65 · C≥50 · D≥35 · E<35；样本、位置空缺及规则校验可限制评级。",
    }
