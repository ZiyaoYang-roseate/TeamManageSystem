import hashlib
import json
from collections import Counter, defaultdict
from itertools import groupby

from constants import SHOT_ZONES
from db import transaction
from validation import BusinessError


METRICS_VERSION = "1"
ZONE_LABELS = dict(zip(SHOT_ZONES, ("篮下", "油漆区", "中距离", "左侧底角三分", "右侧底角三分", "弧顶及侧翼三分")))
AVAILABILITY_LABELS = {"available": "可出战", "questionable": "出战待定", "injured": "因伤缺阵", "rest": "轮休"}
HEALTH_LABELS = {
    "Out": "伤病表标记缺阵", "Questionable": "伤病表标记待定",
    "Day-to-Day": "每日观察", "Probable": "预计可出场",
}
GAME_SQL = """
    SELECT g.id, g.season, g.game_date, g.status, g.period, g.seconds_remaining,
           g.starters_initialized, g.home_team_id, g.away_team_id,
           g.home_score, g.away_score, ht.name AS home_team_name, at.name AS away_team_name
    FROM games g
    JOIN teams ht ON ht.id = g.home_team_id
    JOIN teams at ON at.id = g.away_team_id
"""


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(snapshot):
    return hashlib.sha256(encode([METRICS_VERSION, snapshot]).encode("utf-8")).hexdigest()


def score_for(game, team_id):
    if game["home_team_id"] == team_id:
        return game["home_score"], game["away_score"]
    return game["away_score"], game["home_score"]


def event_clock(event):
    minutes, seconds = divmod(event["seconds_remaining"], 60)
    period = event["period"]
    name = f"第 {period} 节" if period <= 4 else f"第 {period - 4} 个加时"
    return f"{name} {minutes}:{seconds:02d}"


def event_ref(game_id, event, label):
    return {"game_id": game_id, "anchor": f"event-{event['id']}", "label": f"{label} · {event_clock(event)}"}


def evidence(key, title, fact, action, refs=(), recommendation=None):
    item = {"id": key, "title": title, "fact": fact, "actions": list(action), "refs": list(refs)}
    if recommendation:
        item["recommendation"] = recommendation
    return item


def timeline_evidence(game, team_id, events):
    if not events:
        return [], [], [], 0, "缺少逐回合记录，当前复盘仅使用比分、技术统计和已记录的投篮。"
    ordered = sorted(events, key=lambda e: (e["period"], -e["seconds_remaining"], e["id"]))
    previous_home = previous_away = 0
    for event in ordered:
        home, away = event["score_home"], event["score_away"]
        delta_home, delta_away = home - previous_home, away - previous_away
        if min(delta_home, delta_away) < 0 or delta_home + delta_away > 3 or (delta_home and delta_away):
            return [], [], [], 0, "计分事件的累计比分存在跳变，已隐藏分节和领先变化分析。"
        previous_home, previous_away = home, away
    if (previous_home, previous_away) != (game["home_score"], game["away_score"]):
        return [], [], [], 0, "逐回合末次比分与终场比分不一致，已隐藏分节和领先变化分析。"

    is_home = game["home_team_id"] == team_id
    game_id = game["id"]
    previous = (0, 0)
    periods = []
    period_items = []
    points = []
    changes = []
    leader = 0
    for period, group in groupby(ordered, key=lambda event: event["period"]):
        rows = list(group)
        first, last = rows[0], rows[-1]
        final = (last["score_home"], last["score_away"])
        home_points, away_points = final[0] - previous[0], final[1] - previous[1]
        own, other = (home_points, away_points) if is_home else (away_points, home_points)
        label = f"第 {period} 节" if period <= 4 else f"第 {period - 4} 个加时"
        periods.append({"label": label, "ours": own, "theirs": other, "margin": own - other})
        period_items.append(evidence(
            f"period-{period}", f"{label}的得失分",
            f"本队在该节得到 {own} 分，对手得到 {other} 分，分差 {own - other:+d}。",
            ("period", "possessions"),
            (event_ref(game_id, first, "查看该节首条记录"), event_ref(game_id, last, "查看该节末条记录")),
        ))
        previous = final
        for event in rows:
            home, away = event["score_home"], event["score_away"]
            own, other = (home, away) if is_home else (away, home)
            if not points or (own, other) != (points[-1]["ours"], points[-1]["theirs"]):
                points.append({"clock": event_clock(event), "ours": own, "theirs": other})
            side = (own > other) - (own < other)
            if side:
                if leader and side != leader:
                    changes.append((event, side, own, other))
                leader = side

    items = []
    if changes:
        event, side, own, other = changes[-1]
        label = "本队反超" if side > 0 else "对手反超"
        items.append(evidence(
            "lead-change", "最后一次领先方变化",
            f"{event_clock(event)}，{label}，当时本队 {own} 比 {other} 对手。全场共 {len(changes)} 次领先方变化。",
            ("swing", "possessions"), (event_ref(game_id, event, "定位反超回合"),),
            "练习最后一次反超前后的攻防衔接：从该回合前两次进攻和两次防守取样，复盘暂停、换人和进攻落位，再在训练中按当时的比分与时钟重现。",
        ))
    run = strongest_scoring_run(ordered, is_home)
    if run:
        side, amount, first, last = run
        items.append(evidence(
            "scoring-run", "最长连续得分",
            f"{event_clock(first)} 至 {event_clock(last)}，{'本队' if side == 1 else '对手'}连续得到 {amount} 分。",
            ("swing", "possessions"),
            (event_ref(game_id, first, "定位得分段起点"), event_ref(game_id, last, "定位得分段终点")),
        ))
    ranked_periods = sorted(zip(periods, period_items), key=lambda pair: -abs(pair[0]["margin"]))
    items.extend(item for _, item in ranked_periods)
    return items, periods, points, len(changes), None


def strongest_scoring_run(events, is_home):
    best = None
    previous = (0, 0)
    side, amount, first, period = 0, 0, None, None
    for event in events:
        total = (event["score_home"], event["score_away"])
        home, away = total[0] - previous[0], total[1] - previous[1]
        previous = total
        if home == away == 0:
            continue
        scorer = 1 if bool(home) == is_home else -1
        if scorer != side or event["period"] != period:
            side, amount, first, period = scorer, 0, event, event["period"]
        amount += home + away
        if best is None or amount > best[1]:
            best = side, amount, first, event
    return best


def zone_evidence(shots, preview_limit=None, prefix="zone"):
    by_zone = defaultdict(list)
    for shot in shots:
        by_zone[shot["zone"]].append({
            "id": shot["id"], "game_id": shot["game_id"], "clock": event_clock(shot),
            "made": bool(shot["made"]), "x": float(shot["x"]), "y": float(shot["y"]),
        })
    zones = []
    items = []
    for index, (zone, attempts) in enumerate(sorted(by_zone.items(), key=lambda pair: (-len(pair[1]), pair[0]))):
        key = f"{prefix}-{index + 1}"
        made = sum(shot["made"] for shot in attempts)
        label = ZONE_LABELS.get(zone, zone)
        pct = round(made / len(attempts) * 100, 1)
        preview = attempts if preview_limit is None else attempts[-preview_limit:]
        zones.append({"id": key, "label": label, "attempts": len(attempts), "made": made, "pct": pct, "shots": preview})
        items.append(evidence(
            key, f"{label}投篮",
            f"在已有投篮记录里，该区域命中 {made} 球、出手 {len(attempts)} 次，命中率 {pct:.1f}%。",
            ("shot_selection", "contest"),
        ))
    return items, zones


def player_evidence(stats):
    by_player = defaultdict(list)
    for stat in stats:
        by_player[stat["player_id"]].append(stat)
    ranked = sorted(
        by_player.values(),
        key=lambda lines: (-sum(row["points"] for row in lines) / len(lines), lines[0]["player_id"]),
    )[:5]
    items = []
    for lines in ranked:
        player = lines[0]["player_name"]
        average = sum(row["points"] for row in lines) / len(lines)
        label = f"记录场均 {average:.1f} 分，样本 {len(lines)} 场" if len(lines) > 1 else f"本样本记录 {lines[0]['points']} 分"
        refs = [
            {"game_id": row["game_id"], "anchor": f"stat-{row['id']}", "label": f"比赛 #{row['game_id']} 技术统计"}
            for row in lines
        ]
        items.append(evidence(f"player-{lines[0]['player_id']}", player, f"{player} · {label}。", ("player", "possessions"), refs))
    return items


def review_recommendations(game, team_id, periods, timeline, stats, allowed_zones):
    refs = [{"game_id": game["id"], "anchor": "box-score", "label": "双方技术统计"}]
    own = [row for row in stats if row["team_id"] == team_id]
    opponent = [row for row in stats if row["team_id"] != team_id]
    items = []
    if own and opponent:
        for metric, title, minimum, action in (
            ("turnovers", "控制失误", 3, "下次训练加入半场压迫下的出球练习，先区分运球失误和传球失误。下场以少于本场失误次数为复核目标。"),
            ("rebounds", "解决篮板差距", 4, "安排投篮出手后的卡位练习，要求弱侧球员先找人再追球。下场同时记录丢失篮板的回合和篮板差。"),
        ):
            ours, theirs = (sum(row.get(metric, 0) for row in side) for side in (own, opponent))
            disadvantage = ours - theirs if metric == "turnovers" else theirs - ours
            if disadvantage >= minimum:
                items.append(evidence(
                    metric, title, f"技术统计中本队 {ours}，对手 {theirs}，本队在这项差 {disadvantage}。",
                    ("box_score",), refs, action,
                ))
    if periods:
        index, worst = min(enumerate(periods), key=lambda pair: pair[1]["margin"])
        if worst["margin"] <= -5:
            period_items = sorted((item for item in timeline if item["id"].startswith("period-")),
                                  key=lambda item: int(item["id"].split("-")[1]))
            items.append(evidence(
                "rotation", "缩短失分时段", f"{worst['label']}本队 {worst['ours']} 比 {worst['theirs']}，单节净负 {-worst['margin']} 分。",
                ("period",), period_items[index]["refs"],
                f"先回看{worst['label']}失分时段前后的轮换和出手。下场预设连续两个进攻空回合时重新组织战术，再对比对应节次的净分。",
            ))
    items.extend(zone_recommendation(allowed_zones, "review", refs))
    return items


def zone_recommendation(zones, kind, refs=()):
    volume = sum(zone["attempts"] for zone in zones)
    reliable = [zone for zone in zones if zone["attempts"] >= (12 if kind == "scout" else 6)]
    if not reliable:
        return []
    zone = max(reliable, key=lambda row: (row["made"], row["attempts"]))
    if zone["made"] == 0:
        return []
    label = zone["label"]
    if "三分" in label:
        adjustment = "防守先保证该区域有人接管，弱侧协防后安排最近球员补位，追踪该区无人干扰出手。"
    elif label in {"篮下", "油漆区"}:
        adjustment = "优先保护篮下，外线第一道防线引导持球人离开中路，协防人提早站位，追踪该区出手占比。"
    elif label == "中距离":
        adjustment = "挡拆防守试用更靠上的站位，压缩持球人的急停空间，同时记录是否因此放出篮下机会。"
    else:
        adjustment = "训练中复现该区域的防守轮转，并单独记录这个区域的出手次数。"
    time = "本场" if kind == "review" else "赛前历史"
    item = evidence(
        "defensive-zone", f"优先限制{label}出手",
        f"{time}对手在{label} {zone['attempts']} 投 {zone['made']} 中，占已记出手的 {zone['attempts'] / volume:.0%}。",
        ("contest",), refs, adjustment,
    )
    item["zone_id"] = zone["id"]
    return [item]


def build_review(game, team_id, events, stats, shots):
    timeline, periods, points, changes, warning = timeline_evidence(game, team_id, events)
    own, other = score_for(game, team_id)
    final = evidence(
        "final", "终场比分", f"本队 {own} 比 {other} 对手。",
        ("box_score",), ({"game_id": game["id"], "anchor": "box-score", "label": "核对比赛技术统计"},),
    )
    subject_stats = [row for row in stats if row["team_id"] == team_id]
    subject_shots = [row for row in shots if row["team_id"] == team_id]
    zone_items, zones = zone_evidence(subject_shots)
    _, allowed_zones = zone_evidence([row for row in shots if row["team_id"] != team_id], prefix="allowed")
    warnings = [warning] if warning else []
    if not subject_stats:
        warnings.append("本队缺少球员技术统计。")
    elif sum(row["points"] for row in subject_stats) != own:
        warnings.append("本队球员技术统计总得分与终场比分不一致，球员数据按技术统计原表展示。")
    if not subject_shots:
        warnings.append("本队缺少逐次投篮记录。")
    elif subject_stats and sum(row["field_goals_attempted"] for row in subject_stats) != len(subject_shots):
        warnings.append("逐次投篮与技术统计的总出手数不同，分区命中率以逐次投篮记录为样本。")
    return {
        "kind": "review", "team_id": team_id, "game_id": game["id"], "subject": _team_name(game, team_id),
        "score": [own, other], "periods": periods, "timeline": points, "lead_changes": changes,
        "zones": zones + allowed_zones, "warnings": warnings, "samples": [],
        "evidence": [*review_recommendations(game, team_id, periods, timeline, stats, allowed_zones),
                     final, *timeline, *zone_items[:2], *player_evidence(subject_stats), *zone_items[2:]],
    }


def _team_name(game, team_id):
    return game["home_team_name"] if game["home_team_id"] == team_id else game["away_team_name"]


def build_scout(game, team_id, opponent_id, history, stats, shots, availability=(), starters=(), own_profile=None):
    subject = _team_name(game, opponent_id)
    samples = []
    shot_counts = Counter(shot["game_id"] for shot in shots)
    for previous in history:
        own, other = score_for(previous, opponent_id)
        samples.append({
            "id": previous["id"], "date": previous["game_date"].isoformat(),
            "ours": own, "theirs": other,
            "shot_count": shot_counts[previous["id"]],
        })
    zone_items, zones = zone_evidence(shots, preview_limit=60)
    appeared = [row for row in stats if row.get("played_seconds", 1) > 0]
    players = player_evidence(appeared)
    unavailable = {row.get("player_id") for row in availability if row["status"] in {"injured", "rest"}}
    current_roster = {row.get("player_id") for row in availability} if game.get("status") == "scheduled" else set()
    eligible = [
        row for row in appeared if row["player_id"] not in unavailable
        and (not current_roster or row["player_id"] in current_roster)
    ]
    games_by_player = Counter(row["player_id"] for row in eligible)
    leaders = player_evidence([row for row in eligible if games_by_player[row["player_id"]] >= 3])
    items = zone_recommendation(zones, "scout",
                                [{"game_id": row["id"], "anchor": "box-score", "label": f"{row['date']} 比赛详情"} for row in samples[-3:]])
    if leaders:
        leader = leaders[0]
        items.append(evidence(
            "matchup", f"为{leader['title']}预设重点对位", leader["fact"],
            ("player",), leader["refs"][-5:],
            f"如果{leader['title']}确认出战，赛前指定第一对位人和换防接手人。首节前五次该球员参与的进攻单独标记，核对是否需要调整对位。",
        ))
    opponent_stat_games = {row["game_id"] for row in stats}
    if own_profile and own_profile["games"] >= 3 and len(opponent_stat_games) >= 3:
        own_rebounds = float(own_profile["rebounds"])
        opponent_rebounds = sum(row.get("rebounds", 0) for row in stats) / len(opponent_stat_games)
        if opponent_rebounds - own_rebounds >= 3:
            items.append(evidence(
                "rebound-plan", "安排弱侧篮板保护",
                f"赛前有技术统计的比赛中，本队场均 {own_rebounds:.1f} 篮板（{own_profile['games']} 场），"
                f"对手 {opponent_rebounds:.1f}（{len(opponent_stat_games)} 场）。",
                ("possessions",),
                recommendation="投篮出手后给弱侧锋线明确卡位任务，首节统计对手的二次进攻，必要时延后本队外线提前下快攻。",
            ))
    if samples:
        average = sum(row["ours"] for row in samples) / len(samples)
        wins = sum(row["ours"] > row["theirs"] for row in samples)
        items.append(evidence(
            "trend", "对手赛前战绩和场均得分",
            f"截至 {game['game_date']} 之前，系统共记录{subject} {len(samples)} 场完赛、{wins} 胜，场均 {average:.1f} 分。",
            ("trend", "possessions"),
            ({"game_id": row["id"], "anchor": "box-score", "label": f"{row['date']} 比赛详情"} for row in samples),
        ))
    warnings = []
    updates = [
        {
            "name": row["name"], "label": AVAILABILITY_LABELS[row["status"]],
            "source": "本场出勤公告", "updated_at": row["updated_at"].isoformat(sep=" ", timespec="minutes"),
        }
        for row in availability if row["status"] in AVAILABILITY_LABELS
    ]
    health = [
        {"name": row["name"], "label": HEALTH_LABELS[row["injury_status"]], "source": "当前伤病记录", "updated_at": None}
        for row in availability if row["status"] is None and row["injury_status"] in HEALTH_LABELS
    ]
    if updates:
        counts = {status: sum(row["status"] == status for row in availability) for status in AVAILABILITY_LABELS}
        summary = "、".join(f"{label} {counts[status]} 人" for status, label in AVAILABILITY_LABELS.items() if counts[status])
        names = [row["name"] for row in availability if row["status"] in {"injured", "rest", "questionable"}]
        recommendation = f"为{'、'.join(names)}准备缺阵或限时两套对位表，待定人员赛前复核。" if names else None
        items.insert(0, evidence("availability", "对手本场出勤公告", f"{subject}已公布{summary}。", ("availability",),
                                 recommendation=recommendation))
    if len(samples) < 5:
        warnings.append(f"赛前历史样本共 {len(samples)} 场，少于五场。")
    if any(row["shot_count"] == 0 for row in samples):
        warnings.append("部分样本比赛没有对手投篮坐标，分区统计仅覆盖已有记录。")
    if samples and len({row["game_id"] for row in stats}) < len(samples):
        warnings.append("部分样本比赛没有对手球员技术统计。")
    if not updates:
        warnings.append("没有保存对手本场的出勤公告。" if game.get("status") in {"live", "finished"}
                        else "对手尚未发布本场出勤公告，轮休和能否出战仍待确认。")
    if health:
        warnings.append("当前伤病记录供参考，未发布本场公告的球员仍需确认出战。")
    opponent_starters = [row for row in starters if row["team_id"] == opponent_id]
    if len(opponent_starters) == 5:
        lineup = "、".join(row["name"] for row in opponent_starters)
        items.insert(0, evidence("starters", "对手已公布首发", f"{subject}的首发五人为{lineup}。", ("lineup",)))
    return {
        "kind": "scout", "team_id": team_id, "game_id": game["id"], "subject": subject,
        "history_before": game["game_date"].isoformat(),
        "score": None, "periods": [], "timeline": [], "samples": samples,
        "zones": zones, "warnings": warnings, "availability": [*updates, *health], "starters": list(starters),
        "evidence": [*items, *zone_items[:2], *players, *zone_items[2:]],
    }


def get_game(cursor, game_id):
    cursor.execute(GAME_SQL + " WHERE g.id = %s", (game_id,))
    return cursor.fetchone()


def awaiting_starters(game):
    return game["status"] == "scheduled" or (game["status"] == "live" and not game["starters_initialized"])


def published_starters(cursor, game_id, *, lock=False, historical=False):
    if historical:
        cursor.execute(
            """
            SELECT s.team_id, p.id, p.name, p.position
            FROM game_starters s JOIN players p ON p.id = s.player_id
            WHERE s.game_id = %s ORDER BY s.team_id, p.id
            """,
            (game_id,),
        )
        return cursor.fetchall()
    cursor.execute(
        """
        SELECT s.team_id, p.id, p.name, p.position
        FROM game_starters s JOIN players p ON p.id = s.player_id AND p.team_id = s.team_id AND p.archived_at IS NULL
        LEFT JOIN game_player_availability a ON a.game_id = s.game_id AND a.player_id = s.player_id
        WHERE s.game_id = %s AND (a.status IS NULL OR a.team_id <> s.team_id OR a.status NOT IN ('injured', 'rest'))
        ORDER BY s.team_id, p.id
        """ + (" FOR UPDATE" if lock else ""),
        (game_id,),
    )
    return cursor.fetchall()


def _game_rows(cursor, ids, team_id):
    placeholders = ", ".join(["%s"] * len(ids))
    params = (*ids, team_id)
    cursor.execute(
        f"""
        SELECT s.id, s.game_id, s.team_id, s.player_id, s.points,
               s.field_goals_attempted, s.rebounds, s.turnovers,
               GREATEST(s.seconds_played, s.minutes_played * 60) AS played_seconds, p.name AS player_name
        FROM player_game_stats s JOIN players p ON p.id = s.player_id
        WHERE s.game_id IN ({placeholders}) AND s.team_id = %s ORDER BY s.game_id, s.id
        """,
        params,
    )
    stats = cursor.fetchall()
    cursor.execute(
        f"""
        SELECT id, game_id, team_id, zone, period, seconds_remaining, made, x, y
        FROM shot_attempts WHERE game_id IN ({placeholders}) AND team_id = %s AND voided_at IS NULL ORDER BY game_id, id
        """,
        params,
    )
    return stats, cursor.fetchall()


def load_evidence(game_id, team_id, kind):
    with transaction() as connection, connection.cursor() as cursor:
        game = get_game(cursor, game_id)
        if game is None:
            raise BusinessError("比赛不存在", 404)
        if team_id not in (game["home_team_id"], game["away_team_id"]):
            raise BusinessError("该球队未参加本场比赛", 403)
        if kind == "review":
            if game["status"] != "finished":
                raise BusinessError("完赛后可生成赛后复盘。", 409)
            cursor.execute(
                """
                SELECT id, period, seconds_remaining, score_home, score_away
                FROM play_by_play WHERE game_id = %s AND voided_at IS NULL ORDER BY period, seconds_remaining DESC, id
                """,
                (game_id,),
            )
            events = cursor.fetchall()
            opponent = game["away_team_id"] if team_id == game["home_team_id"] else game["home_team_id"]
            stats, shots = _game_rows(cursor, [game_id], team_id)
            opponent_stats, opponent_shots = _game_rows(cursor, [game_id], opponent)
            return build_review(game, team_id, events, [*stats, *opponent_stats], [*shots, *opponent_shots])

        opponent = game["away_team_id"] if team_id == game["home_team_id"] else game["home_team_id"]
        cursor.execute(
            GAME_SQL + """
            WHERE g.status = 'finished' AND g.game_date < %s
              AND (g.home_team_id = %s OR g.away_team_id = %s)
            ORDER BY g.game_date, g.id
            """,
            (game["game_date"], opponent, opponent),
        )
        history = cursor.fetchall()
        stats, shots = _game_rows(cursor, [row["id"] for row in history], opponent) if history else ([], [])
        if game["status"] == "scheduled":
            cursor.execute(
                """
                SELECT p.id AS player_id, p.name, h.injury_status, a.status, a.updated_at
                FROM players p
                LEFT JOIN player_health h ON h.player_id = p.id
                LEFT JOIN game_player_availability a
                    ON a.game_id = %s AND a.player_id = p.id AND a.team_id = p.team_id
                WHERE p.team_id = %s AND p.archived_at IS NULL ORDER BY p.name, p.id
                """,
                (game_id, opponent),
            )
        else:
            cursor.execute(
                """
                SELECT p.id AS player_id, p.name, NULL AS injury_status, a.status, a.updated_at
                FROM game_player_availability a JOIN players p ON p.id = a.player_id
                WHERE a.game_id = %s AND a.team_id = %s ORDER BY p.name, p.id
                """,
                (game_id, opponent),
            )
        availability = cursor.fetchall()
        cursor.execute(
            """
            SELECT COUNT(DISTINCT s.game_id) AS games,
                   SUM(s.rebounds) / NULLIF(COUNT(DISTINCT s.game_id), 0) AS rebounds
            FROM player_game_stats s JOIN games g ON g.id = s.game_id
            WHERE s.team_id = %s AND g.status = 'finished' AND g.game_date < %s
            """,
            (team_id, game["game_date"]),
        )
        own_profile = cursor.fetchone()
        archived = game["status"] == "finished" or bool(game["starters_initialized"])
        return build_scout(
            game, team_id, opponent, history, stats, shots, availability,
            published_starters(cursor, game_id, historical=archived), own_profile,
        )
