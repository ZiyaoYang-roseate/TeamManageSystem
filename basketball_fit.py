METRICS = {
    "points": "得分", "rebounds": "篮板", "assists": "助攻",
    "threes": "三分命中", "turnovers": "失误",
}
PRIORITIES = {
    "rebounds": ("增加篮板保护", "把篮板冲抢和卡位表现作为轮换考察项。"),
    "assists": ("补充组织能力", "用第二阵容的发起回合测试出球和串联能力。"),
    "threes": ("补充外线产量", "用定点接球与无球跑位回合考察外线拉开空间的能力。"),
    "turnovers": ("改善球权保护", "用受压出球和挡拆决策回合复核助攻与失误。"),
}


def player_profiles(cursor, players, season):
    if not players:
        return []
    placeholders = ", ".join(["%s"] * len(players))
    cursor.execute(
        f"""
        SELECT s.player_id, COUNT(*) AS games,
               SUM(GREATEST(s.seconds_played, s.minutes_played * 60)) AS seconds,
               SUM(s.points) AS points, SUM(s.rebounds) AS rebounds,
               SUM(s.assists) AS assists, SUM(s.three_points_made) AS threes,
               SUM(s.turnovers) AS turnovers
        FROM player_game_stats s JOIN games g ON g.id = s.game_id
        WHERE g.season = %s AND g.status = 'finished'
          AND s.player_id IN ({placeholders})
          AND GREATEST(s.seconds_played, s.minutes_played * 60) > 0
        GROUP BY s.player_id
        """,
        (season, *(player["id"] for player in players)),
    )
    samples = {row["player_id"]: row for row in cursor.fetchall()}
    return [{**player, "sample": samples.get(player["id"])} for player in players]


def reliable(player):
    sample = player["sample"]
    return sample is not None and sample["games"] >= 3 and sample["seconds"] >= 3600


def per_36(players, metric):
    seconds = sum(player["sample"]["seconds"] for player in players)
    return float(sum(player["sample"][metric] for player in players) * 2160 / seconds)


def team_priorities(cursor, team_id, season):
    cursor.execute(
        """
        SELECT s.game_id, s.team_id, SUM(s.rebounds) AS rebounds,
               SUM(s.assists) AS assists, SUM(s.three_points_made) AS threes,
               SUM(s.turnovers) AS turnovers
        FROM player_game_stats s JOIN games g ON g.id = s.game_id
        WHERE g.season = %s AND g.status = 'finished'
          AND (g.home_team_id = %s OR g.away_team_id = %s)
        GROUP BY s.game_id, s.team_id
        HAVING SUM(GREATEST(s.seconds_played, s.minutes_played * 60)) > 0
        """,
        (season, team_id, team_id),
    )
    sides = {}
    for row in cursor.fetchall():
        sides.setdefault(row["game_id"], {})[row["team_id"] == team_id] = row
    paired = [side for side in sides.values() if set(side) == {True, False}]
    if len(paired) < 3:
        return len(paired), []
    priorities = []
    for metric in PRIORITIES:
        ours, opponents = (float(sum(side[own][metric] for side in paired) / len(paired)) for own in (True, False))
        gap = ours - opponents if metric == "turnovers" else opponents - ours
        if gap >= 1:
            priorities.append({"metric": metric, "ours": ours, "opponents": opponents, "gap": gap})
    return len(paired), sorted(priorities, key=lambda row: -row["gap"])


def display_profile(player, role):
    sample = player["sample"]
    return {
        "name": player["name"], "position": player["position"], "role": role,
        "games": sample["games"] if sample else 0,
        "minutes": round(float(sample["seconds"] / 60)) if sample else 0,
        "metrics": {metric: round(per_36([player], metric), 1) for metric in METRICS} if sample else {},
        "reliable": reliable(player),
    }
