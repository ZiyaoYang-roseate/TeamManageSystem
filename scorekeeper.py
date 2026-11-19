import hashlib
import json
import math
from datetime import date, datetime
from uuid import UUID

from flask import abort

from caching import invalidate_all
from db import fetch_all, fetch_one, transaction
from game_analysis import awaiting_starters, published_starters
from roster import lock_teams
from validation import BusinessError, bounded_float, bounded_int


SCOREKEEPER_ACTIONS = {
    "add_point": "points",
    "add_rebound": "rebounds",
    "add_assist": "assists",
    "add_steal": "steals",
    "add_block": "blocks",
    "add_turnover": "turnovers",
    "add_foul": "fouls",
}

POINT_EVENT_TYPES = {
    1: "Free Throw Made",
    2: "Two-Point Made",
    3: "Three-Point Made",
}

ACTION_EVENT_TYPES = {
    "add_rebound": "Rebound",
    "add_assist": "Assist",
    "add_steal": "Steal",
    "add_block": "Block",
    "add_turnover": "Turnover",
    "add_foul": "Foul",
}
EVENT_STAT_DELTAS = {
    POINT_EVENT_TYPES[1]: {"points": 1, "free_throws_made": 1, "free_throws_attempted": 1},
    POINT_EVENT_TYPES[2]: {"points": 2, "field_goals_made": 1, "field_goals_attempted": 1},
    POINT_EVENT_TYPES[3]: {"points": 3, "field_goals_made": 1, "field_goals_attempted": 1, "three_points_made": 1},
    "Two-Point Missed": {"field_goals_attempted": 1},
    "Three-Point Missed": {"field_goals_attempted": 1},
    "Free Throw Missed": {"free_throws_attempted": 1},
    **{event_type: {SCOREKEEPER_ACTIONS[action]: 1} for action, event_type in ACTION_EVENT_TYPES.items()},
}

REGULATION_PERIOD_SECONDS = 12 * 60
OVERTIME_PERIOD_SECONDS = 5 * 60
MAX_GAME_PERIOD = 20
COURT_BASELINE_Y = 430
BASKET_Y = 390
THREE_POINT_ARC_RADIUS = 237.5
THREE_POINT_CORNER_X = 220
THREE_POINT_CORNER_START_Y = 290


class ScorekeeperError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def as_date(value, field_name="日期"):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ScorekeeperError(f"{field_name}格式无效")


def date_context(game):
    game_date = as_date(game.get("game_date"), "比赛日期")
    business_date = as_date(game.get("business_date"), "当前比赛日")
    if game_date < business_date:
        relation = "past"
        message = "该场比赛属于历史比赛日，只能查看，不能继续计分"
    elif game_date > business_date:
        relation = "future"
        message = f"该场比赛将在 {game_date.isoformat()} 进行，不能在当前比赛日提前计分"
    else:
        relation = "today"
        message = "该场比赛属于当前比赛日，可以开始或继续计分"
    return {
        "business_date": business_date,
        "game_date": game_date,
        "date_relation": relation,
        "can_score_today": relation == "today",
        "date_message": message,
    }


def _require_current_scorekeeper_date(game):
    context = date_context(game)
    if context["date_relation"] == "past":
        raise ScorekeeperError("该场比赛已超过当前比赛日，不能继续计分", 409)
    if context["date_relation"] == "future":
        raise ScorekeeperError("该场比赛尚未到比赛日，不能提前计分", 409)
    return context


def _require_initialized_starters(game):
    if not game["starters_initialized"]:
        raise ScorekeeperError("请先等双方球队提交首发五人，再载入首发开始计分。", 409)


def _scorekeeper_game_or_404(game_id):
    game = fetch_one(
        """
        SELECT
            g.id,
            g.season,
            g.game_date,
            g.home_team_id,
            g.away_team_id,
            g.home_score,
            g.away_score,
            g.status,
            g.starters_initialized,
            g.period,
            g.seconds_remaining,
            bd.business_date,
            bd.updated_at AS business_date_updated_at,
            ht.name AS home_team_name,
            ht.abbreviation AS home_team_abbr,
            at.name AS away_team_name,
            at.abbreviation AS away_team_abbr
        FROM games g
        JOIN scorekeeper_business_dates bd ON bd.season = g.season
        JOIN teams ht ON ht.id = g.home_team_id
        JOIN teams at ON at.id = g.away_team_id
        WHERE g.id = %s
        """,
        (game_id,),
    )
    if game is None:
        abort(404)
    return game


def _scorekeeper_roster(game_id, team_id):
    return fetch_all(
        """
        SELECT
            p.id,
            p.name,
            p.jersey_number,
            p.position,
            p.team_id,
            COALESCE(s.minutes_played, 0) AS minutes_played,
            COALESCE(s.points, 0) AS points,
            COALESCE(s.rebounds, 0) AS rebounds,
            COALESCE(s.assists, 0) AS assists,
            COALESCE(s.steals, 0) AS steals,
            COALESCE(s.blocks, 0) AS blocks,
            COALESCE(s.turnovers, 0) AS turnovers,
            COALESCE(s.fouls, 0) AS fouls,
            COALESCE(s.seconds_played, 0) AS seconds_played,
            COALESCE(s.is_on_court, 0) AS is_on_court,
            COALESCE(s.field_goals_made, 0) AS field_goals_made,
            COALESCE(s.field_goals_attempted, 0) AS field_goals_attempted,
            COALESCE(s.three_points_made, 0) AS three_points_made,
            COALESCE(s.free_throws_made, 0) AS free_throws_made,
            COALESCE(s.free_throws_attempted, 0) AS free_throws_attempted,
            COALESCE(s.plus_minus, 0) AS plus_minus
        FROM players p
        LEFT JOIN player_game_stats s
            ON s.player_id = p.id AND s.game_id = %s
        WHERE p.team_id = %s AND p.archived_at IS NULL
        ORDER BY p.jersey_number, p.id
        """,
        (game_id, team_id),
    )


def _scorekeeper_events(game_id, limit=40):
    return fetch_all(
        """
        SELECT
            pbp.id,
            pbp.period,
            pbp.seconds_remaining,
            pbp.team_id,
            pbp.player_id,
            pbp.event_type,
            pbp.description,
            pbp.voided_at, pbp.correction_reason, pbp.recording IS NOT NULL AS can_reverse,
            pbp.score_home,
            pbp.score_away,
            p.name AS player_name,
            t.abbreviation AS team_abbr
        FROM play_by_play pbp
        JOIN teams t ON t.id = pbp.team_id
        LEFT JOIN players p ON p.id = pbp.player_id
        WHERE pbp.game_id = %s
        ORDER BY pbp.id DESC
        LIMIT %s
        """,
        (game_id, limit),
    )


def game_state(game_id):
    game = _scorekeeper_game_or_404(game_id)
    starters = []
    needs_starters = awaiting_starters(game)
    if needs_starters:
        with transaction() as connection, connection.cursor() as cursor:
            starters = published_starters(cursor, game_id)
    ready_teams = [
        team_id for team_id in (game["home_team_id"], game["away_team_id"])
        if sum(row["team_id"] == team_id for row in starters) == 5
    ]
    events = _scorekeeper_events(game_id)
    latest_event = next((event for event in events if not event["voided_at"]), None)
    return {
        "game": game,
        **date_context(game),
        "home_stats": _scorekeeper_roster(game_id, game["home_team_id"]),
        "away_stats": _scorekeeper_roster(game_id, game["away_team_id"]),
        "events": events,
        "reversible_event_id": latest_event["id"] if latest_event and latest_event["can_reverse"] else None,
        "published_starters": [row for row in starters if row["team_id"] in ready_teams],
        "starter_ready_teams": ready_teams,
        "starters_ready": len(ready_teams) == 2,
        "needs_starters": needs_starters,
    }


def _coerce_int(value, field_name, default=None):
    if value is None:
        if default is None:
            raise ScorekeeperError(f"{field_name}不能为空")
        return default
    try:
        return bounded_int(value, field_name, -2_147_483_648)
    except BusinessError as exc:
        raise ScorekeeperError(str(exc)) from exc


def _coerce_float(value, field_name):
    if value is None:
        raise ScorekeeperError(f"{field_name}不能为空")
    try:
        return bounded_float(value, field_name, -math.inf, math.inf)
    except BusinessError as exc:
        raise ScorekeeperError(str(exc)) from exc


def _classify_shot_zone(x, y):
    distance_from_basket = math.hypot(x, y - BASKET_Y)
    if _is_three_point_location(x, y):
        if x < -THREE_POINT_CORNER_X and y >= THREE_POINT_CORNER_START_Y:
            return "Left Corner 3PT"
        if x > THREE_POINT_CORNER_X and y >= THREE_POINT_CORNER_START_Y:
            return "Right Corner 3PT"
        return "Above The Break 3PT"
    if distance_from_basket <= 56:
        return "Restricted Area"
    if abs(x) <= 80 and 240 <= y <= COURT_BASELINE_Y:
        return "In The Paint (Non-RA)"
    return "Mid-Range"


def _is_three_point_location(x, y):
    """Return whether a court coordinate is strictly outside the 3-point line."""
    if y >= THREE_POINT_CORNER_START_Y:
        return abs(x) > THREE_POINT_CORNER_X
    return math.hypot(x, y - BASKET_Y) > THREE_POINT_ARC_RADIUS


def _validate_shot_location(points, x, y):
    if not -250 <= x <= 250 or not 0 <= y <= COURT_BASELINE_Y:
        raise ScorekeeperError("投篮坐标超出有效半场范围")

    is_three_point_location = _is_three_point_location(x, y)
    if points == 3 and not is_three_point_location:
        raise ScorekeeperError("三分投篮必须选择三分线外的位置")
    if points == 2 and is_three_point_location:
        raise ScorekeeperError("两分投篮必须选择三分线内的位置")


def _default_shot_type(points, zone):
    if points == 3:
        return "Three-Point"
    if zone == "Restricted Area":
        return "Layup"
    if zone == "In The Paint (Non-RA)":
        return "Floater"
    if zone == "Mid-Range":
        return "Pull-up"
    return "Jump Shot"


def record_event(game_id, payload):
    player_id = _coerce_int(payload.get("player_id"), "球员")
    action = payload.get("action")
    if not isinstance(action, str) or (
        action not in SCOREKEEPER_ACTIONS and action not in {"add_miss", "add_free_throw_miss"}
    ):
        raise ScorekeeperError("不支持的操作类型")
    request_id = payload.get("request_id")
    request_hash = None
    if request_id is not None:
        try:
            request_id = str(UUID(request_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ScorekeeperError("计分请求 ID 无效") from exc
        event_data = {key: payload.get(key) for key in ("player_id", "action", "value", "shot_x", "shot_y")}
        request_hash = hashlib.sha256(json.dumps(event_data, sort_keys=True).encode()).hexdigest()

    is_score_action = action == "add_point"
    is_miss_action = action == "add_miss"
    is_free_throw_miss_action = action == "add_free_throw_miss"
    if is_score_action:
        points = _coerce_int(payload.get("value"), "得分值")
        if points not in POINT_EVENT_TYPES:
            raise ScorekeeperError("得分值只支持 1、2 或 3 分")
    elif is_miss_action:
        points = _coerce_int(payload.get("value"), "投篮分值")
        if points not in (2, 3):
            raise ScorekeeperError("投失必须明确选择两分或三分")
    elif is_free_throw_miss_action:
        points = None
    else:
        raw_value = payload.get("value", 1)
        value = _coerce_int(raw_value, "计数值", 1)
        if value != 1:
            raise ScorekeeperError("非得分操作每次只能增加 1 次")

    shot_x = shot_y = shot_zone = shot_type = shot_distance = None
    if is_miss_action or (is_score_action and points in (2, 3)):
        shot_x = _coerce_float(payload.get("shot_x"), "投篮 X 坐标")
        shot_y = _coerce_float(payload.get("shot_y"), "投篮 Y 坐标")
        _validate_shot_location(points, shot_x, shot_y)
        shot_zone = _classify_shot_zone(shot_x, shot_y)
        shot_type = _default_shot_type(points, shot_zone)
        shot_distance = int(math.hypot(shot_x, shot_y - BASKET_Y))

    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT g.id, g.season, g.game_date, g.home_team_id,
                       g.away_team_id, g.home_score, g.away_score,
                       g.status, g.period, g.seconds_remaining,
                       g.starters_initialized, bd.business_date
                FROM games g
                JOIN scorekeeper_business_dates bd ON bd.season = g.season
                WHERE g.id = %s
                FOR UPDATE
                """,
                (game_id,),
            )
            game = cursor.fetchone()
            if game is None:
                raise ScorekeeperError("比赛不存在", 404)
            if request_id is not None:
                cursor.execute(
                    """
                    SELECT description, event_type, score_home, score_away, request_hash, voided_at
                    FROM play_by_play WHERE game_id = %s AND request_id = %s
                    """,
                    (game_id, request_id),
                )
                previous = cursor.fetchone()
                if previous is not None:
                    if previous["request_hash"] != request_hash:
                        raise ScorekeeperError("计分请求 ID 已用于其他事件", 409)
                    if previous["voided_at"]:
                        raise ScorekeeperError("这条计分事件已撤销，请作为新事件重新录入", 409)
                    return {
                        "message": previous["description"],
                        "game": {"home_score": previous["score_home"], "away_score": previous["score_away"]},
                        "player_id": player_id,
                        "player_name": previous["description"].removesuffix(f" {previous['event_type']}"),
                    }
            if game["status"] == "finished":
                raise ScorekeeperError("比赛已结束，无法继续计分", 409)
            _require_current_scorekeeper_date(game)
            if game["status"] == "scheduled":
                raise ScorekeeperError("比赛尚未开始，请先开始比赛", 409)
            _require_initialized_starters(game)

            cursor.execute(
                """
                SELECT team_id, COUNT(*) AS active_count, GROUP_CONCAT(player_id ORDER BY player_id) AS player_ids
                FROM player_game_stats
                WHERE game_id = %s AND is_on_court = 1
                GROUP BY team_id
                """,
                (game_id,),
            )
            lineups = cursor.fetchall()
            active_counts = {
                row["team_id"]: row["active_count"]
                for row in lineups
            }
            if active_counts.get(game["home_team_id"], 0) != 5 or active_counts.get(game["away_team_id"], 0) != 5:
                raise ScorekeeperError("每支球队必须且只能有 5 名球员同时在场")

            on_court_ids = [
                int(player_id) for row in lineups for player_id in row["player_ids"].split(",")
            ] if is_score_action else []
            cursor.execute(
                """
                SELECT id, name, team_id
                FROM players
                WHERE id = %s
                FOR UPDATE
                """,
                (player_id,),
            )
            player = cursor.fetchone()
            if player is None:
                raise ScorekeeperError("球员不存在", 404)
            if player["team_id"] not in (game["home_team_id"], game["away_team_id"]):
                raise ScorekeeperError("该球员不属于本场比赛双方球队")

            cursor.execute(
                """
                INSERT INTO player_game_stats
                    (game_id, player_id, team_id, minutes_played, points, rebounds,
                     assists, steals, blocks, turnovers, fouls, field_goals_made,
                     field_goals_attempted, three_points_made, free_throws_made,
                     free_throws_attempted, plus_minus)
                VALUES (%s, %s, %s, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
                ON DUPLICATE KEY UPDATE id = id
                """,
                (game_id, player_id, player["team_id"]),
            )
            cursor.execute(
                "SELECT is_on_court FROM player_game_stats WHERE game_id = %s AND player_id = %s",
                (game_id, player_id),
            )
            stat_row = cursor.fetchone()
            if not stat_row or not stat_row["is_on_court"]:
                raise ScorekeeperError("该球员当前不在场上")

            if is_score_action:
                if points == 1:
                    cursor.execute(
                        """
                        UPDATE player_game_stats
                        SET points = points + %s,
                            free_throws_made = free_throws_made + 1,
                            free_throws_attempted = free_throws_attempted + 1
                        WHERE game_id = %s AND player_id = %s
                        """,
                        (points, game_id, player_id),
                    )
                elif points == 2:
                    cursor.execute(
                        """
                        UPDATE player_game_stats
                        SET points = points + %s,
                            field_goals_made = field_goals_made + 1,
                            field_goals_attempted = field_goals_attempted + 1
                        WHERE game_id = %s AND player_id = %s
                        """,
                        (points, game_id, player_id),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE player_game_stats
                        SET points = points + %s,
                            field_goals_made = field_goals_made + 1,
                            field_goals_attempted = field_goals_attempted + 1,
                            three_points_made = three_points_made + 1
                        WHERE game_id = %s AND player_id = %s
                        """,
                        (points, game_id, player_id),
                    )

                if player["team_id"] == game["home_team_id"]:
                    cursor.execute(
                        "UPDATE games SET home_score = home_score + %s WHERE id = %s",
                        (points, game_id),
                    )
                else:
                    cursor.execute(
                        "UPDATE games SET away_score = away_score + %s WHERE id = %s",
                        (points, game_id),
                    )
                cursor.execute(
                    """
                    UPDATE player_game_stats
                    SET plus_minus = plus_minus + CASE
                        WHEN team_id = %s THEN %s
                        ELSE -%s
                    END
                    WHERE game_id = %s AND is_on_court = 1
                    """,
                    (player["team_id"], points, points, game_id),
                )
            elif is_miss_action:
                cursor.execute(
                    """
                    UPDATE player_game_stats
                    SET field_goals_attempted = field_goals_attempted + 1
                    WHERE game_id = %s AND player_id = %s
                    """,
                    (game_id, player_id),
                )
            elif is_free_throw_miss_action:
                cursor.execute(
                    """
                    UPDATE player_game_stats
                    SET free_throws_attempted = free_throws_attempted + 1
                    WHERE game_id = %s AND player_id = %s
                    """,
                    (game_id, player_id),
                )
            else:
                column = SCOREKEEPER_ACTIONS[action]
                cursor.execute(
                    f"""
                    UPDATE player_game_stats
                    SET {column} = {column} + 1
                    WHERE game_id = %s AND player_id = %s
                    """,
                    (game_id, player_id),
                )

            cursor.execute(
                "SELECT home_score, away_score FROM games WHERE id = %s",
                (game_id,),
            )
            score_after = cursor.fetchone()

            if is_miss_action:
                event_type = "Three-Point Missed" if points == 3 else "Two-Point Missed"
                description = f"{player['name']} {event_type}"
            elif is_free_throw_miss_action:
                event_type = "Free Throw Missed"
                description = f"{player['name']} {event_type}"
            elif is_score_action:
                event_type = POINT_EVENT_TYPES[points]
                description = f"{player['name']} {event_type}"
            else:
                event_type = ACTION_EVENT_TYPES[action]
                description = f"{player['name']} {event_type}"

            cursor.execute(
                """
                INSERT INTO play_by_play
                    (game_id, period, seconds_remaining, team_id, player_id,
                     assist_player_id, event_type, description,
                     score_home, score_away, request_id, request_hash, recording)
                VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    game_id,
                    game["period"],
                    game["seconds_remaining"],
                    player["team_id"],
                    player_id,
                    event_type,
                    description,
                    score_after["home_score"],
                    score_after["away_score"],
                    request_id,
                    request_hash,
                    json.dumps({"on_court_ids": on_court_ids}),
                ),
            )
            event_id = cursor.lastrowid if shot_x is not None else None

            if event_id is not None:
                cursor.execute(
                    """
                    INSERT INTO shot_attempts
                        (game_id, player_id, team_id, period, seconds_remaining,
                         x, y, distance_cm, shot_type, zone, made)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        game_id,
                        player_id,
                        player["team_id"],
                        game["period"],
                        game["seconds_remaining"],
                        shot_x,
                        shot_y,
                        shot_distance,
                        shot_type,
                        shot_zone,
                        0 if is_miss_action else 1,
                    ),
                )
                cursor.execute("UPDATE play_by_play SET recording = %s WHERE id = %s",
                               (json.dumps({"on_court_ids": on_court_ids, "shot_id": cursor.lastrowid}), event_id))

        result = {
            "message": description,
            "game": score_after,
            "player_id": player_id,
            "player_name": player["name"],
        }
    invalidate_all()
    return result


def reverse_event(game_id, event_id, user_id, reason):
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 255:
        raise ScorekeeperError("请填写 1 至 255 个字符的更正原因")
    with transaction() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT g.*, bd.business_date FROM games g
            JOIN scorekeeper_business_dates bd ON bd.season = g.season
            WHERE g.id = %s FOR UPDATE
            """,
            (game_id,),
        )
        game = cursor.fetchone()
        if game is None:
            raise ScorekeeperError("比赛不存在", 404)
        _require_current_scorekeeper_date(game)
        if game["status"] != "live":
            raise ScorekeeperError("只可在比赛进行中更正事件", 409)
        _require_initialized_starters(game)
        cursor.execute("SELECT voided_at FROM play_by_play WHERE id = %s AND game_id = %s", (event_id, game_id))
        target = cursor.fetchone()
        if target is None:
            raise ScorekeeperError("比赛事件不存在", 404)
        if target["voided_at"]:
            return {"message": "该事件已经撤销"}
        cursor.execute(
            """
            SELECT id, player_id, team_id, event_type, recording FROM play_by_play
            WHERE game_id = %s AND voided_at IS NULL ORDER BY id DESC LIMIT 1
            """,
            (game_id,),
        )
        event = cursor.fetchone()
        if not event or event["id"] != event_id or not event["recording"]:
            raise ScorekeeperError("只可撤销最新一条有更正记录支持的事件", 409)
        changes = EVENT_STAT_DELTAS.get(event["event_type"])
        if not changes or event["player_id"] is None:
            raise ScorekeeperError("该事件缺少可用于更正的球员统计", 409)
        metadata = json.loads(event["recording"])
        assignments = ", ".join(f"{column} = {column} - %s" for column in changes)
        enough = " AND ".join(f"{column} >= %s" for column in changes)
        cursor.execute(
            f"UPDATE player_game_stats SET {assignments} WHERE game_id = %s AND player_id = %s AND {enough}",
            (*changes.values(), game_id, event["player_id"], *changes.values()),
        )
        if cursor.rowcount != 1:
            raise ScorekeeperError("球员统计与原事件不一致，未撤销", 409)
        points = changes.get("points", 0)
        if points:
            score_column = "home_score" if event["team_id"] == game["home_team_id"] else "away_score"
            if game[score_column] < points:
                raise ScorekeeperError("当前比分与原事件不一致，未撤销", 409)
            cursor.execute(f"UPDATE games SET {score_column} = {score_column} - %s WHERE id = %s", (points, game_id))
            ids = metadata["on_court_ids"]
            if len(ids) != 10 or len(set(ids)) != 10:
                raise ScorekeeperError("原事件缺少完整的在场名单，未撤销", 409)
            placeholders = ", ".join(["%s"] * len(ids))
            cursor.execute(
                f"""
                UPDATE player_game_stats SET plus_minus = plus_minus + CASE WHEN team_id = %s THEN -%s ELSE %s END
                WHERE game_id = %s AND player_id IN ({placeholders})
                """,
                (event["team_id"], points, points, game_id, *ids),
            )
            if cursor.rowcount != 10:
                raise ScorekeeperError("原事件的在场统计不完整，未撤销", 409)
        if "field_goals_attempted" in changes:
            cursor.execute("UPDATE shot_attempts SET voided_at = NOW() WHERE id = %s AND game_id = %s AND voided_at IS NULL",
                           (metadata.get("shot_id"), game_id))
            if cursor.rowcount != 1:
                raise ScorekeeperError("找不到对应投篮，未撤销", 409)
        cursor.execute(
            "UPDATE play_by_play SET voided_at = NOW(), voided_by = %s, correction_reason = %s WHERE id = %s",
            (user_id, reason.strip(), event_id),
        )
    invalidate_all()
    return {"message": "已撤销事件，原记录与更正原因已保留"}


def set_lineup(game_id, payload):
    team_id = _coerce_int(payload.get("team_id"), "球队")
    raw_player_ids = payload.get("player_ids")
    if not isinstance(raw_player_ids, list) or len(raw_player_ids) != 5:
        raise ScorekeeperError("每支球队必须提交 5 名在场球员")

    player_ids = []
    for raw_player_id in raw_player_ids:
        player_ids.append(_coerce_int(raw_player_id, "球员"))
    if len(set(player_ids)) != 5:
        raise ScorekeeperError("在场球员不能重复")

    placeholders = ", ".join(["%s"] * len(player_ids))
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT g.id, g.season, g.game_date, g.home_team_id,
                       g.away_team_id, g.status, g.starters_initialized, bd.business_date
                FROM games g
                JOIN scorekeeper_business_dates bd ON bd.season = g.season
                WHERE g.id = %s
                FOR UPDATE
                """,
                (game_id,),
            )
            game = cursor.fetchone()
            if game is None:
                raise ScorekeeperError("比赛不存在", 404)
            if game["status"] == "finished":
                raise ScorekeeperError("比赛已结束，无法调整阵容", 409)
            _require_current_scorekeeper_date(game)
            if game["status"] != "live":
                raise ScorekeeperError("首发由球队在赛前分析发布，开赛后才能记录换人", 409)
            _require_initialized_starters(game)
            if team_id not in (game["home_team_id"], game["away_team_id"]):
                raise ScorekeeperError("球队不属于本场比赛")

            cursor.execute(
                f"""
                SELECT id, team_id
                FROM players
                WHERE id IN ({placeholders}) AND archived_at IS NULL
                FOR UPDATE
                """,
                tuple(player_ids),
            )
            players = cursor.fetchall()
            if len(players) != 5 or any(row["team_id"] != team_id for row in players):
                raise ScorekeeperError("在场球员必须都属于所选球队")

            _set_on_court(cursor, game_id, team_id, player_ids)

    invalidate_all()
    return {
        "message": "在场阵容已更新",
        "game_id": game_id,
        "team_id": team_id,
        "player_ids": player_ids,
    }


def _set_on_court(cursor, game_id, team_id, player_ids):
    cursor.execute(
        "UPDATE player_game_stats SET is_on_court = 0 WHERE game_id = %s AND team_id = %s",
        (game_id, team_id),
    )
    cursor.executemany(
        """
        INSERT INTO player_game_stats
            (game_id, player_id, team_id, minutes_played, points,
             rebounds, assists, steals, blocks, turnovers, fouls,
             seconds_played, is_on_court, field_goals_made, field_goals_attempted,
             three_points_made, free_throws_made, free_throws_attempted, plus_minus)
        VALUES (%s, %s, %s, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0)
        ON DUPLICATE KEY UPDATE team_id = VALUES(team_id), is_on_court = 1
        """,
        [(game_id, player_id, team_id) for player_id in player_ids],
    )


def _elapsed_game_seconds(period, seconds_remaining):
    if period <= 4:
        return (
            (period - 1) * REGULATION_PERIOD_SECONDS
            + REGULATION_PERIOD_SECONDS
            - seconds_remaining
        )
    return (
        4 * REGULATION_PERIOD_SECONDS
        + (period - 5) * OVERTIME_PERIOD_SECONDS
        + OVERTIME_PERIOD_SECONDS
        - seconds_remaining
    )


def _validate_overtime_transition(game, period, seconds_remaining):
    if period <= 4 or period <= game["period"]:
        return
    if game["period"] < 4:
        raise ScorekeeperError("必须先完成第四节，才能进入加时赛")
    if period != game["period"] + 1:
        raise ScorekeeperError("加时赛必须逐节进入，不能跳过节次")
    if game["seconds_remaining"] != 0:
        raise ScorekeeperError("当前节比赛时间尚未结束，不能进入加时赛")
    if game["home_score"] != game["away_score"]:
        raise ScorekeeperError("当前比分未打平，不能进入加时赛")
    if seconds_remaining != OVERTIME_PERIOD_SECONDS:
        raise ScorekeeperError("新加时赛必须从 5:00 开始")


def update_clock(game_id, payload):
    period = _coerce_int(payload.get("period"), "节次")
    seconds_remaining = _coerce_int(payload.get("seconds_remaining"), "剩余秒数")
    if not 1 <= period <= MAX_GAME_PERIOD:
        raise ScorekeeperError(f"节次必须在 1 到 {MAX_GAME_PERIOD} 之间")
    period_seconds = (
        REGULATION_PERIOD_SECONDS if period <= 4 else OVERTIME_PERIOD_SECONDS
    )
    if not 0 <= seconds_remaining <= period_seconds:
        period_label = "常规节" if period <= 4 else "加时赛"
        raise ScorekeeperError(
            f"{period_label}剩余秒数必须在 0 到 {period_seconds} 之间"
        )

    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT g.id, g.season, g.game_date, g.status, g.period,
                       g.seconds_remaining, g.home_team_id, g.away_team_id,
                       g.home_score, g.away_score, g.starters_initialized, bd.business_date
                FROM games g
                JOIN scorekeeper_business_dates bd ON bd.season = g.season
                WHERE g.id = %s
                FOR UPDATE
                """,
                (game_id,),
            )
            game = cursor.fetchone()
            if game is None:
                raise ScorekeeperError("比赛不存在", 404)
            _require_current_scorekeeper_date(game)
            if game["status"] != "live":
                raise ScorekeeperError("比赛尚未开始，无法调整时间", 409)
            _require_initialized_starters(game)

            _validate_overtime_transition(game, period, seconds_remaining)
            if period > game["period"] and period <= 4:
                if period != game["period"] + 1 or game["seconds_remaining"] != 0:
                    raise ScorekeeperError("本节时钟归零后才能按顺序进入下一节", 409)
                if seconds_remaining != REGULATION_PERIOD_SECONDS:
                    raise ScorekeeperError("下一节须从 12:00 开始", 409)

            cursor.execute(
                """
                SELECT team_id, COUNT(*) AS active_count
                FROM player_game_stats
                WHERE game_id = %s AND is_on_court = 1
                GROUP BY team_id
                """,
                (game_id,),
            )
            active_counts = {
                row["team_id"]: row["active_count"]
                for row in cursor.fetchall()
            }
            if active_counts.get(game["home_team_id"], 0) != 5 or active_counts.get(game["away_team_id"], 0) != 5:
                raise ScorekeeperError("每支球队必须且只能有 5 名球员同时在场")

            old_elapsed = _elapsed_game_seconds(
                game["period"], game["seconds_remaining"]
            )
            new_elapsed = _elapsed_game_seconds(period, seconds_remaining)
            if new_elapsed < old_elapsed:
                raise ScorekeeperError("比赛时间不能倒退")

            cursor.execute(
                """
                UPDATE games
                SET period = %s, seconds_remaining = %s
                WHERE id = %s
                """,
                (period, seconds_remaining, game_id),
            )

            delta = new_elapsed - old_elapsed
            if delta:
                cursor.execute(
                    """
                    UPDATE player_game_stats
                    SET seconds_played = seconds_played + %s
                    WHERE game_id = %s AND is_on_court = 1
                    """,
                    (delta, game_id),
                )
                cursor.execute(
                    """
                    UPDATE player_game_stats
                    SET minutes_played = FLOOR(seconds_played / 60)
                    WHERE game_id = %s AND is_on_court = 1
                    """,
                    (game_id,),
                )

            cursor.execute(
                """
                SELECT id, status, period, seconds_remaining
                FROM games
                WHERE id = %s
                """,
                (game_id,),
            )
            game_after = cursor.fetchone()

    invalidate_all()
    return {
        "message": "比赛时间已更新",
        "game": game_after,
    }


def finish_game(game_id, *, user_id=None, note="", check_date=True):
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT g.id, g.season, g.game_date, g.status, g.home_score,
                       g.away_score, g.period, g.seconds_remaining,
                       g.starters_initialized, bd.business_date
                FROM games g
                JOIN scorekeeper_business_dates bd ON bd.season = g.season
                WHERE g.id = %s
                FOR UPDATE
                """,
                (game_id,),
            )
            game = cursor.fetchone()
            if game is None:
                raise ScorekeeperError("比赛不存在", 404)
            if game["status"] == "finished":
                return {"message": "比赛已结束", "game": game, "already_finished": True}
            if check_date:
                _require_current_scorekeeper_date(game)
            if game["status"] != "live":
                raise ScorekeeperError("比赛尚未开始，不能结束比赛", 409)
            _require_initialized_starters(game)
            if game["home_score"] == game["away_score"]:
                raise ScorekeeperError("当前比分相同，需录入分出胜负的比分后才能完赛", 409)

            cursor.execute(
                "UPDATE games SET status = 'finished' WHERE id = %s",
                (game_id,),
            )
            if user_id is not None and note and (not check_date or game["period"] < 4 or game["seconds_remaining"] != 0):
                cursor.execute(
                    """
                    INSERT INTO league_game_reviews
                        (game_id,status,finding,resolution,opened_by,resolved_by,resolved_at)
                    VALUES (%s,'resolved',%s,%s,%s,%s,NOW())
                    ON DUPLICATE KEY UPDATE resolution=VALUES(resolution),resolved_by=VALUES(resolved_by),
                        status='resolved',resolved_at=NOW()
                    """,
                    (game_id, "按已录入比分完赛", note, user_id, user_id),
                )
            cursor.execute(
                """
                SELECT id, status, home_score, away_score, period, seconds_remaining
                FROM games
                WHERE id = %s
                """,
                (game_id,),
            )
            game_after = cursor.fetchone()

    invalidate_all()
    return {
        "message": "比赛已结束，缓存已刷新",
        "game": game_after,
        "already_finished": False,
    }


def start_game(game_id):
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT home_team_id, away_team_id, status FROM games WHERE id = %s", (game_id,))
            fixture = cursor.fetchone()
            if fixture is None:
                raise ScorekeeperError("比赛不存在", 404)
            if fixture["status"] in {"finished", "cancelled"}:
                raise ScorekeeperError("比赛已完赛或取消，无法开赛", 409)
            try:
                lock_teams(cursor, fixture["home_team_id"], fixture["away_team_id"])
            except BusinessError as exc:
                raise ScorekeeperError(str(exc), exc.status_code) from exc
            cursor.execute(
                """
                SELECT g.id, g.season, g.status, g.home_score, g.away_score,
                       g.game_date, g.period, g.seconds_remaining,
                       g.home_team_id, g.away_team_id, g.starters_initialized, bd.business_date
                FROM games g
                JOIN scorekeeper_business_dates bd ON bd.season = g.season
                WHERE g.id = %s
                FOR UPDATE
                """,
                (game_id,),
            )
            game = cursor.fetchone()
            if game is None:
                raise ScorekeeperError("比赛不存在", 404)
            if game["status"] == "finished":
                raise ScorekeeperError("比赛已结束，无法重新开始", 409)
            if game["status"] == "cancelled":
                raise ScorekeeperError("比赛已取消，无法开赛", 409)
            resuming = game["status"] == "live"
            if resuming and game["starters_initialized"]:
                return {
                    "message": "比赛已在进行中",
                    "game": game,
                    "already_started": True,
                }
            _require_current_scorekeeper_date(game)

            if not resuming:
                cursor.execute(
                    "SELECT game_date FROM games WHERE status = 'live' AND id <> %s FOR UPDATE",
                    (game_id,),
                )
                if any(row["game_date"] != game["game_date"] for row in cursor.fetchall()):
                    raise ScorekeeperError("只能同时开始同一天的比赛", 409)

            starters = published_starters(cursor, game_id, lock=True)
            lineups = {
                team_id: [row["id"] for row in starters if row["team_id"] == team_id]
                for team_id in (game["home_team_id"], game["away_team_id"])
            }
            if any(len(player_ids) != 5 for player_ids in lineups.values()):
                raise ScorekeeperError("等待双方球队发布有效的首发五人名单后才能开赛", 409)
            # An older live game may already have substitutions and recorded events.
            keep_live_lineup = False
            if resuming:
                cursor.execute("SELECT 1 FROM play_by_play WHERE game_id = %s LIMIT 1", (game_id,))
                keep_live_lineup = bool(cursor.fetchone() or game["home_score"] or game["away_score"])
            if not keep_live_lineup:
                for team_id, player_ids in lineups.items():
                    _set_on_court(cursor, game_id, team_id, player_ids)

            if resuming:
                cursor.execute("UPDATE games SET starters_initialized = 1 WHERE id = %s", (game_id,))
            else:
                cursor.execute(
                    """
                    UPDATE games SET status = 'live', starters_initialized = 1,
                        home_score = 0, away_score = 0, period = 1, seconds_remaining = 720
                    WHERE id = %s
                    """,
                    (game_id,),
                )
            cursor.execute(
                """
                SELECT id, status, home_score, away_score, game_date, period, seconds_remaining
                FROM games
                WHERE id = %s
                """,
                (game_id,),
            )
            game_after = cursor.fetchone()

    invalidate_all()
    return {
        "message": "双方首发已确认，可以继续计分" if resuming else "比赛已开始",
        "game": game_after,
        "already_started": False,
    }
