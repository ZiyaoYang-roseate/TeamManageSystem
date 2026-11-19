from db import fetch_all


LEADER_METRICS = {
    "points": ("得分", "分"),
    "rebounds": ("篮板", "个"),
    "assists": ("助攻", "次"),
    "steals": ("抢断", "次"),
    "blocks": ("盖帽", "次"),
    "threes": ("三分命中", "个"),
}


def directory_data(season, metric="points", mode="average", tab="ranking", search="", page=1, per_page=20):
    rows = fetch_all(
        """
        WITH season_stats AS (
            SELECT s.player_id,COUNT(*) AS games,
                   SUM(CASE WHEN s.seconds_played>0 THEN s.seconds_played ELSE s.minutes_played*60 END)/60 AS minutes,
                   SUM(s.points) AS points,SUM(s.rebounds) AS rebounds,SUM(s.assists) AS assists,
                   SUM(s.steals) AS steals,SUM(s.blocks) AS blocks,SUM(s.three_points_made) AS threes
            FROM player_game_stats s JOIN games g ON g.id=s.game_id
            WHERE g.season=%s AND g.status='finished'
              AND (s.minutes_played>0 OR s.seconds_played>0 OR s.points>0 OR s.rebounds>0 OR s.assists>0
                    OR s.steals>0 OR s.blocks>0 OR s.turnovers>0 OR s.fouls>0 OR s.field_goals_attempted>0 OR s.free_throws_attempted>0)
            GROUP BY s.player_id
        )
        SELECT p.*,COALESCE(t.name,'自由球员') AS team_name,COALESCE(t.abbreviation,'FA') AS abbreviation,
               COALESCE(s.games,0) AS games,s.minutes,
               s.points,s.rebounds,s.assists,s.steals,s.blocks,s.threes,t.archived_at AS team_archived_at
        FROM players p LEFT JOIN teams t ON t.id=p.team_id
        LEFT JOIN season_stats s ON s.player_id=p.id ORDER BY p.id
        """,
        (season,),
    )
    for row in rows:
        for key in ("minutes", *LEADER_METRICS):
            value = row[key]
            row[key] = (
                round(float(value) / row["games"], 1) if mode == "average" and row["games"]
                else round(float(value), 1) if value is not None else None
            )
    participants = [row for row in rows if row["games"]]
    boards = [
        {"metric": key, "label": label, "unit": unit,
         "players": sorted(participants, key=lambda row: (-row[key], -row["games"], row["id"]))[:5]}
        for key, (label, unit) in LEADER_METRICS.items()
    ]
    selected = rows if tab == "all" else participants
    query = search.casefold()
    if query:
        selected = [
            row for row in selected
            if query in " ".join(str(row[key]) for key in ("name", "team_name", "abbreviation", "position")).casefold()
        ]
    selected = sorted(selected, key=lambda row: (
        row[metric] is None, -(row[metric] or 0), -row["games"], row["id"]
    ))
    total = len(selected)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    start = (page - 1) * per_page
    return {"boards": boards, "rows": selected[start:start+per_page], "total": total, "page": page,
            "total_pages": pages, "offset": start}


def team_members(team_id, season):
    return fetch_all(
        """
        WITH history AS (
            SELECT s.player_id,COUNT(*) AS games,ROUND(AVG(s.points),1) AS ppg,
                   ROUND(AVG(s.rebounds),1) AS rpg,ROUND(AVG(s.assists),1) AS apg
            FROM player_game_stats s JOIN games g ON g.id=s.game_id
            WHERE s.team_id=%s AND g.season=%s AND g.status='finished'
              AND (s.minutes_played>0 OR s.seconds_played>0 OR s.points>0 OR s.rebounds>0 OR s.assists>0 OR s.steals>0 OR s.blocks>0 OR s.turnovers>0
                    OR s.fouls>0 OR s.field_goals_attempted>0 OR s.free_throws_attempted>0)
            GROUP BY s.player_id
        )
        SELECT p.id,p.name,p.position,p.jersey_number,p.archived_at,p.team_id,
               p.team_id=%s AS is_current,COALESCE(t.name,'自由球员') AS current_team,
               COALESCE(h.games,0) AS games,h.ppg,h.rpg,h.apg
        FROM players p LEFT JOIN teams t ON t.id=p.team_id LEFT JOIN history h ON h.player_id=p.id
        WHERE p.team_id=%s AND p.archived_at IS NULL
        ORDER BY p.position,p.jersey_number,p.id
        """,
        (team_id,season,team_id,team_id),
    )
