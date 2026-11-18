from db import fetch_all, fetch_one


ZONE_MIN_ATTEMPTS = 10
DETAIL_PAGE_SIZE = 20


def recent_shooting_games(player_id, season):
    rows = fetch_all(
        """
        SELECT g.id
        FROM shot_attempts s JOIN games g ON g.id = s.game_id
        WHERE s.player_id=%s AND g.season=%s AND g.status='finished' AND s.voided_at IS NULL
        GROUP BY g.id, g.game_date
        ORDER BY g.game_date DESC, g.id DESC
        LIMIT 5
        """,
        (player_id, season),
    )
    return [row["id"] for row in rows]


def annotate_zones(zones):
    total = sum(row["attempts"] for row in zones)
    for row in zones:
        attempts = row["attempts"]
        row["share"] = round(100 * attempts / total, 1) if total and attempts else 0
        row["volume_band"] = (
            "empty" if not attempts else "high" if row["share"] >= 25 else "medium" if row["share"] >= 10 else "low"
        )
        row["qualified"] = min(attempts, row["league_attempts"]) >= ZONE_MIN_ATTEMPTS
        difference = row["diff"]
        row["efficiency_band"] = (
            "empty" if not attempts else "limited" if not row["qualified"]
            else "higher" if difference >= 5 else "lower" if difference <= -5 else "near"
        )
    return total


def zone_shots(player_id, season, zone, game_ids, outcome, page):
    if game_ids == []:
        return [], 0, 1
    filters = ["s.player_id=%s", "g.season=%s", "g.status='finished'", "s.zone=%s", "s.voided_at IS NULL"]
    args = [player_id, season, zone]
    if game_ids is not None:
        filters.append("g.id IN (" + ",".join(["%s"] * len(game_ids)) + ")")
        args.extend(game_ids)
    if outcome in {"made", "missed"}:
        filters.append("s.made=%s")
        args.append(int(outcome == "made"))
    matches = "FROM shot_attempts s JOIN games g ON g.id=s.game_id WHERE " + " AND ".join(filters)
    count = fetch_one("SELECT COUNT(*) AS total " + matches, tuple(args))["total"]
    if not count:
        return [], 0, 1
    last_page = (count + DETAIL_PAGE_SIZE - 1) // DETAIL_PAGE_SIZE
    page = min(page, last_page)
    rows = fetch_all(
        f"""
        WITH shot_page AS (
            SELECT s.id, s.game_id, s.player_id, s.team_id, s.period, s.seconds_remaining,
                   s.made, g.game_date, g.home_team_id, g.away_team_id
            {matches}
            ORDER BY g.game_date DESC, g.id DESC, s.period DESC, s.seconds_remaining, s.id DESC
            LIMIT %s OFFSET %s
        )
        SELECT sp.*, active_team.name AS team_name, opponent.name AS opponent_name,
               (SELECT e.id FROM play_by_play e
                WHERE e.game_id=sp.game_id AND e.player_id=sp.player_id AND e.team_id=sp.team_id
                  AND e.voided_at IS NULL
                  AND JSON_UNQUOTE(JSON_EXTRACT(e.recording, '$.shot_id'))=CAST(sp.id AS CHAR)
                ORDER BY e.id LIMIT 1) AS event_id,
               (SELECT CASE WHEN COUNT(*)=1 THEN MIN(e.id) END
                FROM play_by_play e
                WHERE e.game_id=sp.game_id AND e.player_id=sp.player_id AND e.team_id=sp.team_id
                  AND e.period=sp.period AND e.seconds_remaining=sp.seconds_remaining
                  AND e.recording IS NULL AND e.voided_at IS NULL
                  AND ((sp.made=1 AND e.event_type IN ('Two-Point Made', 'Three-Point Made'))
                    OR (sp.made=0 AND e.event_type IN ('Missed Shot', 'Two-Point Missed', 'Three-Point Missed')))
                  AND NOT EXISTS (
                      SELECT 1 FROM shot_attempts other
                      WHERE other.game_id=sp.game_id AND other.player_id=sp.player_id AND other.team_id=sp.team_id
                        AND other.period=sp.period AND other.seconds_remaining=sp.seconds_remaining
                        AND other.made=sp.made AND other.voided_at IS NULL AND other.id<>sp.id
                  )
               ) AS clock_event_id
        FROM shot_page sp
        JOIN teams active_team ON active_team.id=sp.team_id
        JOIN teams opponent ON opponent.id=CASE WHEN sp.team_id=sp.home_team_id THEN sp.away_team_id ELSE sp.home_team_id END
        ORDER BY sp.game_date DESC, sp.game_id DESC, sp.period DESC, sp.seconds_remaining, sp.id DESC
        """,
        (*args, DETAIL_PAGE_SIZE, (page - 1) * DETAIL_PAGE_SIZE),
    )
    return rows, count, page
