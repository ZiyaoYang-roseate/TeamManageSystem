from collections import defaultdict

from db import fetch_all, fetch_one
from roster import season_salary_sql


def _averages(games):
    if not games:
        return None
    keys = ("points", "rebounds", "assists", "steals", "blocks", "turnovers", "plus_minus")
    result = {key: round(sum(row[key] for row in games) / len(games), 1) for key in keys}
    result["games"] = len(games)
    seconds = sum(row["played_seconds"] for row in games)
    result["total_seconds"] = seconds
    result["minutes"] = round(seconds / (len(games) * 60), 1)
    result["total_minutes"] = round(seconds / 60, 1)
    made = sum(row["field_goals_made"] for row in games)
    attempts = sum(row["field_goals_attempted"] for row in games)
    free_throws = sum(row["free_throws_attempted"] for row in games)
    result["fg_pct"] = round(made / attempts * 100, 1) if attempts else None
    denominator = 2 * (attempts + 0.44 * free_throws)
    result["ts_pct"] = round(sum(row["points"] for row in games) / denominator * 100, 1) if denominator else None
    result["threes"] = round(sum(row["three_points_made"] for row in games) / len(games), 1)
    result["per_36"] = {
        key: round(sum(row[key] for row in games) * 2160 / seconds, 1) if seconds else None
        for key in (*keys, "three_points_made")
    }
    return result


def _game_log(player_id, season=None):
    season_filter = "AND g.season = %s" if season else ""
    parameters = (player_id, season) if season else (player_id,)
    return fetch_all(
        f"""
        SELECT g.id AS game_id, g.game_date, g.season, s.team_id,
               t.name AS played_for,
               CASE WHEN s.team_id = g.home_team_id THEN away.name ELSE home.name END AS opponent,
               CASE WHEN s.team_id = g.home_team_id THEN g.home_score ELSE g.away_score END AS team_score,
               CASE WHEN s.team_id = g.home_team_id THEN g.away_score ELSE g.home_score END AS opponent_score,
               CASE WHEN s.seconds_played > 0 THEN s.seconds_played ELSE s.minutes_played * 60 END AS played_seconds,
               s.points, s.rebounds, s.assists, s.steals, s.blocks, s.turnovers, s.plus_minus,
               s.field_goals_made, s.field_goals_attempted, s.three_points_made, s.free_throws_attempted
        FROM player_game_stats s JOIN games g ON g.id = s.game_id
        JOIN teams t ON t.id = s.team_id
        JOIN teams home ON home.id = g.home_team_id
        JOIN teams away ON away.id = g.away_team_id
        WHERE s.player_id = %s AND g.status = 'finished'
          {season_filter}
          AND s.team_id IN (g.home_team_id, g.away_team_id)
          AND (s.seconds_played > 0 OR s.minutes_played > 0 OR s.points > 0 OR s.field_goals_attempted > 0
               OR s.free_throws_attempted > 0 OR s.rebounds > 0 OR s.assists > 0 OR s.steals > 0
               OR s.blocks > 0 OR s.turnovers > 0 OR s.fouls > 0)
        ORDER BY g.game_date DESC, g.id DESC
        """,
        parameters,
    )


def player_comparison_sample(player_id, season):
    games = _game_log(player_id, season)
    return _averages(games), _averages(games[:5])


def compare_box_scores(left, right, mode):
    metrics = [
        ("points", "得分", 0),
        ("rebounds", "篮板", 1.5),
        ("assists", "助攻", 1.0),
        ("three_points_made", "三分命中", 0.7),
        ("turnovers", "失误", 0),
        ("steals", "抢断", 0),
        ("blocks", "盖帽", 0),
        ("fg_pct", "投篮命中率", 0),
        ("ts_pct", "真实命中率", 0),
    ]
    followups = {
        "rebounds": "对照真实轮换位置，回看卡位和防守篮板回合。",
        "assists": "回看挡拆和受压出球，连同失误一起评估组织角色。",
        "three_points_made": "结合出手区域和样本量，复核接球投篮能否为队友拉开空间。",
    }
    normalized = mode == "per36"
    comparable = all(sample and sample["games"] >= 3 and sample["total_seconds"] >= 3600 for sample in (left, right))
    rows, cues = [], []
    for key, label, threshold in metrics:
        percent = key.endswith("_pct")
        values = []
        for sample in (left, right):
            if sample is None:
                values.append(None)
            elif normalized and not percent:
                values.append(sample["per_36"][key])
            else:
                values.append(sample["threes" if key == "three_points_made" else key])
        first, second = values
        difference = round(first - second, 1) if first is not None and second is not None else None
        rows.append({
            "label": label, "percent": percent, "first": first, "second": second,
            "difference": difference if comparable else None,
        })
        if comparable and threshold and difference is not None and abs(difference) >= threshold:
            cues.append({
                "label": label, "side": "A" if difference > 0 else "B",
                "gap": abs(difference), "followup": followups[key],
            })
    return rows, cues, comparable


def player_performance(player_id, season):
    games = _game_log(player_id)
    by_season = defaultdict(list)
    for row in games:
        by_season[row["season"]].append(row)
    current = by_season.get(season, [])
    recent = current[:5]
    return {
        "summary": _averages(current),
        "recent": recent,
        "recent_average": _averages(recent),
        "seasons": [
            {"season": label, **_averages(rows)}
            for label, rows in sorted(by_season.items(), reverse=True)
        ],
    }


def player_contract(player_id, team_id, season):
    year = int(season.split("-", 1)[0])
    return fetch_one(
        f"""
        SELECT c.start_year, c.end_year, c.team_option, c.player_option,
               {season_salary_sql()} AS season_salary
        FROM contracts c
        WHERE c.player_id = %s AND c.team_id = %s AND c.status = 'Active'
          AND c.start_year <= %s AND c.end_year >= %s
        """,
        (year, player_id, team_id, year, year),
    )
