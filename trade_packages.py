"""Bounded search of one to three players on each side of a trade."""
from collections import Counter, defaultdict
from itertools import combinations

from basketball_fit import METRICS, display_profile, per_36, reliable
from trade import salary_matching_max


SCALES = {"points": 12, "rebounds": 5, "assists": 4, "threes": 2, "turnovers": 3}


def _groups(roster):
    groups = []
    depth = Counter(p["position"] for p in roster)
    for size in range(1, min(3, len(roster)) + 1):
        for players in combinations(roster, size):
            supported = [p for p in players if reliable(p)]
            removed = Counter(p["position"] for p in players)
            groups.append({
                "players": players, "ids": tuple(sorted(p["id"] for p in players)),
                "salary": sum(int(p["salary"]) for p in players),
                "depth": removed,
                "emptied": tuple(pos for pos, count in removed.items() if count == depth[pos]),
                "rates": {key: per_36(supported, key) for key in SCALES} if supported else {},
                "supported": len(supported),
            })
    return groups


def ranked_packages(team_id, rosters, payroll, cap, priorities, pending, reserve_slot=False):
    own = rosters[team_id]
    if not 5 <= len(own) <= 15:
        return []
    own_depth = Counter(p["position"] for p in own)
    own_groups = _groups(own)
    metrics = [r["metric"] for r in priorities] or ["rebounds", "assists", "threes", "turnovers", "points"]
    shortlist = defaultdict(list)
    for rival, roster in rosters.items():
        if rival == team_id or len(roster) > 15:
            continue
        incoming_groups = _groups(roster)
        own_space, rival_space = cap-payroll.get(team_id, 0), cap-payroll.get(rival, 0)
        for group in incoming_groups:
            group["matching_limit"] = salary_matching_max(group["salary"], rival_space < 0, rival_space)
        for outgoing in own_groups:
            outgoing_limit = salary_matching_max(outgoing["salary"], own_space < 0, own_space)
            for incoming in incoming_groups:
                sent, received = outgoing["players"], incoming["players"]
                if not 5 <= len(own)-len(sent)+len(received) <= (14 if reserve_slot else 15):
                    continue
                if not 5 <= len(roster)-len(received)+len(sent) <= 15:
                    continue
                if (rival, outgoing["ids"], incoming["ids"]) in pending:
                    continue
                if incoming["salary"] > outgoing_limit or outgoing["salary"] > incoming["matching_limit"]:
                    continue
                if any(not incoming["depth"][pos] for pos in outgoing["emptied"]):
                    continue
                if any(not outgoing["depth"][pos] for pos in incoming["emptied"]):
                    continue
                fills = sum(own_depth[pos] < 2 and incoming["depth"][pos] > outgoing["depth"][pos] for pos in incoming["depth"])
                before, after = outgoing["rates"], incoming["rates"]
                salary_change = incoming["salary"]-outgoing["salary"]
                metric = None
                if before and after:
                    gains = {key:(after[key]-before[key])*(-1 if key=="turnovers" else 1)/SCALES[key] for key in metrics}
                    metric = max(metrics, key=lambda key:gains[key])
                    if gains[metric] <= 0.02 and not (fills and salary_change<=0):
                        continue
                    score = sum(gains[key]/(1+.35*i) for i,key in enumerate(metrics))
                    score -= .35*max(0,before["points"]-after["points"])/12
                    score -= .25*((len(sent)-outgoing["supported"])+(len(received)-incoming["supported"]))
                elif fills and salary_change<=0:
                    score = 0
                else:
                    continue
                score += .5*fills-salary_change/15_000_000-.12*max(0,len(sent)-len(received))
                if score <= 0:
                    continue
                key = (rival,len(sent),len(received))
                bucket = shortlist[key]
                if len(bucket) == 3 and score <= bucket[-1][0]:
                    continue
                bucket.append((score,outgoing,incoming,metric))
                # Keep the best three per opponent and package shape, before DB validation.
                bucket.sort(key=lambda row:(-row[0],row[1]["ids"],row[2]["ids"]))
                del bucket[3:]
    ranked = sorted((r for bucket in shortlist.values() for r in bucket), key=lambda row:(-row[0],row[1]["ids"],row[2]["ids"]))
    # Preserve package variety in the model's shortlist when legal alternatives exist.
    first, rest, seen = [], [], set()
    for item in ranked:
        shape = (len(item[1]["players"]),len(item[2]["players"]))
        (rest if shape in seen else first).append(item)
        seen.add(shape)
    return first+rest


def package_plan(payload, outgoing, incoming, metric, salary, own, rival, priorities, payroll, tax_line):
    from roster_recommendations import COURT_ROLES, _money, _team_needs

    sent,received=outgoing["players"],incoming["players"]
    depth=Counter(p["position"] for p in own)
    after_depth=depth.copy()
    after_depth.subtract(outgoing["depth"]);after_depth.update(incoming["depth"])
    change=salary["incoming"]-salary["outgoing"]
    total=payroll+change
    budget=(f"赛季薪资 {_money(payroll)} → {_money(total)}，{'增加' if change>0 else '节省'} {_money(abs(change))}。"
            f"交易后工资帽空间 {_money(salary['cap_space_after'])}；"
            + (f"超奢侈税线 {_money(total-tax_line)}。" if total>tax_line else f"距奢侈税线 {_money(tax_line-total)}。"))
    options={}
    before,after=outgoing["rates"],incoming["rates"]
    keys=list(dict.fromkeys([metric,*[p["metric"] for p in priorities],*COURT_ROLES]))
    for key in keys:
        if key not in COURT_ROLES or not before or not after:
            continue
        gain=(after[key]-before[key])*(-1 if key=="turnovers" else 1)
        if gain<=0:
            continue
        action,summary,usage,verify=COURT_ROLES[key]
        roles="；".join(f"{p['name']}（{p['position']}）历史每36分钟{METRICS[key]} {per_36([p],key):.1f}" for p in received if reliable(p))
        options[action]={"summary":summary,"usage":usage+"根据换入球员各自的位置分配职责，先确认轮换时间。",
            "court":roles+f"。两组有效样本按出场时间加权，每36分钟{METRICS[key]} {before[key]:.1f} → {after[key]:.1f}；这是历史能力线索，不能视为球队未来数据的直接增量。",
            "verify":verify}
        if len(options)==3:break
    if not options:
        options={"roster_depth":{"summary":"调整轮换深度与预算","usage":"为换入球员分别安排轮换考察，先验证位置职责。","court":"有效技术样本不足，以位置覆盖和薪资约束为依据。","verify":"补看录像并进行对抗试训，再决定正式轮换。"}}
    rotation="本队登记位置："+"、".join(f"{pos} {depth[pos]}→{after_depth[pos]}人" for pos in sorted(set(depth)|set(after_depth)))+f"；总人数 {len(own)}→{len(own)-len(sent)+len(received)} 人。"
    rival_after=Counter(p["position"] for p in rival)
    rival_after.subtract(incoming["depth"]);rival_after.update(outgoing["depth"])
    tradeoff="多人调整会同时改变持球分工、替补深度和磨合成本，需给每位换入球员安排可用时间，并检查换出球员承担的得分、防守和组织职责能否补上。对方仍需自行评估并同意，系统不保证接受。"
    evidence={"送出":[display_profile(p,"送出") for p in sent],"换入":[display_profile(p,"换入") for p in received]}
    return {"title":"送出 "+"、".join(p["name"] for p in sent)+"，换入 "+"、".join(p["name"] for p in received),
        "subtitle":f"交易对手 · {received[0]['team_name']} · {len(sent)} 换 {len(received)}",
        "value":{**next(iter(options.values())),"rotation":rotation,"budget":budget,"tradeoff":tradeoff},
        "court_options":options,"payload":payload,"profiles":[*evidence["送出"],*evidence["换入"]],
        "model_fact":str(evidence)+str(_team_needs(priorities))+rotation+budget+f"对方交易后位置人数 {dict(rival_after)}。"+tradeoff}
