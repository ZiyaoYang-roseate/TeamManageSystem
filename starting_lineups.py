from itertools import combinations


POSITIONS = ("PG", "SG", "SF", "PF", "C")


def lineup_candidates(cursor, game, team_id, snapshot):
    cursor.execute(
        """
        SELECT p.id,p.name,p.position,a.status,h.injury_status
        FROM players p
        LEFT JOIN game_player_availability a ON a.game_id=%s AND a.player_id=p.id AND a.team_id=p.team_id
        LEFT JOIN player_health h ON h.player_id=p.id
        WHERE p.team_id=%s AND p.archived_at IS NULL ORDER BY p.id
        """, (game["id"], team_id),
    )
    players = cursor.fetchall()
    eligible = [
        row for row in players if row["status"] not in {"injured", "rest"}
        and not (row["status"] is None and row["injury_status"] == "Out" and game["status"] == "scheduled")
    ]
    if len(eligible) < 5:
        return [], "当前可用球员不足五人，请先核对出勤公告。"
    cursor.execute(
        """
        SELECT s.player_id,COUNT(*) AS games,
               SUM(CASE WHEN s.seconds_played>0 THEN s.seconds_played ELSE s.minutes_played*60 END) AS seconds,
               SUM(s.points) AS points,SUM(s.assists) AS assists,SUM(s.rebounds) AS rebounds,
               SUM(s.three_points_made) AS threes,SUM(s.turnovers) AS turnovers
        FROM player_game_stats s JOIN games g ON g.id=s.game_id
        WHERE g.status='finished' AND g.game_date<%s
          AND s.player_id IN (SELECT id FROM players WHERE team_id=%s AND archived_at IS NULL)
          AND (s.seconds_played>0 OR s.minutes_played>0)
        GROUP BY s.player_id
        """, (game["game_date"], team_id),
    )
    samples = {row["player_id"]: row for row in cursor.fetchall()}
    inside = sum(row["attempts"] for row in snapshot["zones"] if row["label"] in {"篮下", "油漆区"})
    perimeter = sum(row["attempts"] for row in snapshot["zones"] if "三分" in row["label"])
    emphasis = "篮板保护" if inside > perimeter else "组织与外线接应"
    for row in eligible:
        sample = samples.get(row["id"])
        row["games"] = sample["games"] if sample else 0
        row["minutes"] = round(float(sample["seconds"]) / 60, 1) if sample else 0
        row["rates"] = {
            key: round(float(sample[key]) * 2160 / float(sample["seconds"]), 1)
            for key in ("points", "assists", "rebounds", "threes", "turnovers")
        } if sample and sample["seconds"] else {}
        rate = row["rates"]
        row["score"] = (
            rate.get("points", 0) * .18 + rate.get("assists", 0) * .6
            + rate.get("rebounds", 0) * (.7 if emphasis == "篮板保护" else .35)
            + rate.get("threes", 0) * .65 - rate.get("turnovers", 0) * .3
            + min(row["minutes"], 240) / 120
            - (3 if row["status"] == "questionable" else 0)
        )
    # Evaluate all five-player groups. Position coverage and available samples matter before individual production.
    ranked = sorted(
        combinations(eligible, 5),
        key=lambda group: (
            -len({player["position"] for player in group}),
            -sum(player["score"] for player in group),
            tuple(player["id"] for player in group),
        ),
    )[:6]
    result = []
    for index, group in enumerate(ranked, 1):
        ordered = sorted(group, key=lambda player: (POSITIONS.index(player["position"]), player["id"]))
        selected = [
            {key: row[key] for key in ("id", "name", "position", "status", "games", "minutes", "rates")}
            for row in ordered
        ]
        explanation = (
            f"对手已记录出手中，篮下及油漆区 {inside} 次、三分区 {perimeter} 次，优先考虑{emphasis}。"
            if inside + perimeter else "对手缺少落点样本，先以位置覆盖和本队历史轮换为依据。"
        )
        explanation += "待定球员赛前需再次确认出勤。" if any(row["status"] == "questionable" for row in group) else ""
        result.append({
            "id": f"starters-{index}", "players": selected,
            "reason": explanation,
            "coverage": len({row["position"] for row in group}),
        })
    return result, None


def add_lineup_evidence(cursor, game, team_id, snapshot):
    proposals, warning = lineup_candidates(cursor, game, team_id, snapshot)
    snapshot["starting_lineups"] = proposals
    if warning:
        snapshot["warnings"].append(warning)
    evidence = []
    for proposal in proposals:
        details = []
        for player in proposal["players"]:
            rates = player["rates"]
            details.append(
                f"{player['position']} {player['name']}：赛前 {player['games']} 场、{player['minutes']} 分钟，"
                + (f"每36分钟得分 {rates['points']}、助攻 {rates['assists']}、篮板 {rates['rebounds']}、"
                   f"三分命中 {rates['threes']}、失误 {rates['turnovers']}。" if rates else "暂无有效历史样本。")
            )
        evidence.append({
            "id": proposal["id"], "title": "推荐首发候选",
            "fact": " ".join(details),
            "recommendation": proposal["reason"],
            "actions": ["starting_lineup"], "refs": [],
        })
    snapshot["evidence"] = evidence + snapshot["evidence"]
    return snapshot
