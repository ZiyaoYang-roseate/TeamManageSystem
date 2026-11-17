from db import fetch_all, fetch_one


def league_overview(season):
    setting = fetch_one("SELECT business_date FROM scorekeeper_business_dates WHERE season = %s", (season,))
    business_date = setting["business_date"] if setting else None
    games = fetch_all(
        """
        WITH starter_counts AS (
            SELECT s.game_id, s.team_id, COUNT(*) AS total
            FROM game_starters s JOIN games sg ON sg.id = s.game_id
            JOIN players p ON p.id = s.player_id AND p.team_id = s.team_id
            LEFT JOIN game_player_availability a ON a.game_id = s.game_id AND a.player_id = s.player_id AND a.team_id = s.team_id
            WHERE sg.season = %s AND (a.status IS NULL OR a.status NOT IN ('injured', 'rest'))
            GROUP BY s.game_id, s.team_id
        )
        SELECT g.id, g.game_date, g.status, g.starters_initialized, g.period, g.seconds_remaining,
               g.home_team_id, g.away_team_id, g.home_score, g.away_score,
               h.name AS home, a.name AS away, h.abbreviation AS home_abbr, a.abbreviation AS away_abbr,
               COALESCE(hs.total, 0) AS home_starters, COALESCE(ast.total, 0) AS away_starters,
               EXISTS (
                   SELECT 1
                   FROM game_analysis_snapshots s JOIN game_analysis_reports r ON r.snapshot_id = s.id
                   WHERE s.game_id = g.id AND s.kind = 'review' AND r.completed_at IS NOT NULL AND r.content IS NOT NULL
               ) AS has_review,
               EXISTS (SELECT 1 FROM play_by_play pb WHERE pb.game_id = g.id AND pb.period >= 4 AND pb.voided_at IS NULL) AS has_late_events
        FROM games g
        JOIN teams h ON h.id = g.home_team_id
        JOIN teams a ON a.id = g.away_team_id
        LEFT JOIN starter_counts hs ON hs.game_id = g.id AND hs.team_id = g.home_team_id
        LEFT JOIN starter_counts ast ON ast.game_id = g.id AND ast.team_id = g.away_team_id
        WHERE g.season = %s
        ORDER BY g.game_date, g.id
        """,
        (season, season),
    )
    live = [game for game in games if game["status"] == "live"]
    finished = [game for game in reversed(games) if game["status"] == "finished"]
    scheduled = [game for game in games if game["status"] == "scheduled"]
    next_date = next((game["game_date"] for game in scheduled
                      if business_date is None or game["game_date"] > business_date), None)
    watch_through = next_date or business_date
    lineup = [
        game for game in [*live, *scheduled]
        if not game["starters_initialized"]
        and (game["status"] == "live" or (watch_through and game["game_date"] <= watch_through))
        and (game["home_starters"] != 5 or game["away_starters"] != 5)
    ]
    missing_reports = [game for game in finished if not game["has_review"]]
    missing_clock = [
        game for game in finished
        if game["period"] == 1 and game["seconds_remaining"] == 720 and game["has_late_events"]
    ]
    missing_clock_ids = {game["id"] for game in missing_clock}
    clock_flags = [
        game for game in finished
        if (game["period"] < 4 or game["seconds_remaining"] != 0) and game["id"] not in missing_clock_ids
    ]
    managers = fetch_all(
        """
        SELECT t.name, t.id, t.abbreviation, u.id AS manager_id
        FROM teams t LEFT JOIN users u ON u.team_id = t.id AND u.role = 'management'
        ORDER BY t.abbreviation
        """
    )
    return {
        "business_date": business_date, "next_date": next_date, "live": live[:6], "lineup": lineup[:8],
        "missing_reports": missing_reports[:6], "clock_flags": clock_flags[:6],
        "live_count": len(live), "lineup_count": len(lineup), "missing_report_count": len(missing_reports),
        "clock_flag_count": len(clock_flags), "missing_clock_count": len(missing_clock), "teams_count": len(managers),
        "unassigned": [team for team in managers if team["manager_id"] is None],
    }
