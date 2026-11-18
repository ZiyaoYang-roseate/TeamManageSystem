import argparse
from collections import Counter, defaultdict
from datetime import timedelta

from db import transaction
from seed_data import SEASONS, TEAMS, demo_schedule


def legacy_boxscores(cursor):
    schedule = demo_schedule([team[2] for team in TEAMS])[:45]
    expected = {
        (season, start + timedelta(days=index), home, away)
        for season, start in SEASONS for index, (home, away) in enumerate(schedule)
    }
    cursor.execute(
        """
        SELECT g.*, h.abbreviation AS home, a.abbreviation AS away
        FROM games g JOIN teams h ON h.id=g.home_team_id JOIN teams a ON a.id=g.away_team_id
        WHERE g.status='finished' AND g.period=1 AND g.seconds_remaining=720 AND g.starters_initialized=0
        ORDER BY g.id FOR UPDATE
        """
    )
    changes = []
    for game in cursor.fetchall():
        if (game["season"], game["game_date"], game["home"], game["away"]) not in expected:
            continue
        cursor.execute(
            """
            SELECT player_id,team_id,assist_player_id,period,seconds_remaining,event_type,
                   score_home,score_away,request_id,recording,voided_at
            FROM play_by_play WHERE game_id=%s ORDER BY period,seconds_remaining DESC,id
            """,
            (game["id"],),
        )
        events = cursor.fetchall()
        if not events or any(row["request_id"] or row["recording"] or row["voided_at"] for row in events):
            continue
        if (events[-1]["score_home"], events[-1]["score_away"]) != (game["home_score"], game["away_score"]):
            continue
        cursor.execute("SELECT id,player_id,team_id FROM player_game_stats WHERE game_id=%s", (game["id"],))
        stats = cursor.fetchall()
        if Counter(row["team_id"] for row in stats) != {game["home_team_id"]: 10, game["away_team_id"]: 10}:
            continue

        derived = defaultdict(Counter)
        attempts = Counter()
        legacy_types = {"Missed Shot", "Free Throw Made", "Two-Point Made", "Three-Point Made"}
        if any(row["event_type"] not in legacy_types for row in events):
            continue
        totals = Counter()
        active_players = {(row["team_id"], row["player_id"]) for row in stats}
        valid = True
        for row in events:
            team_id, player_id = row["team_id"], row["player_id"]
            outcome = row["event_type"]
            assist_id = row["assist_player_id"]
            if (team_id, player_id) not in active_players or (assist_id and (team_id, assist_id) not in active_players):
                valid = False
                break
            player = derived[team_id, player_id]
            points = {"Missed Shot": 0, "Free Throw Made": 1, "Two-Point Made": 2, "Three-Point Made": 3}[outcome]
            player["points"] += points
            totals[team_id] += points
            if points == 1:
                player["ftm"] += 1
            else:
                player["fga"] += 1
                player["fgm"] += int(points > 0)
                player["threes"] += int(points == 3)
                attempts[team_id, player_id, row["period"], row["seconds_remaining"], int(points > 0)] += 1
                if assist_id and assist_id != player_id and points > 0:
                    derived[team_id, assist_id]["assists"] += 1
        if not valid or (totals[game["home_team_id"]], totals[game["away_team_id"]]) != (game["home_score"], game["away_score"]):
            continue

        cursor.execute(
            "SELECT team_id,player_id,period,seconds_remaining,made,voided_at FROM shot_attempts WHERE game_id=%s",
            (game["id"],),
        )
        shots = cursor.fetchall()
        observed = Counter((row["team_id"], row["player_id"], row["period"], row["seconds_remaining"], row["made"])
                           for row in shots if not row["voided_at"])
        if len(shots) != sum(observed.values()) or observed != attempts:
            continue
        updates = []
        for row in stats:
            player = derived[row["team_id"], row["player_id"]]
            updates.append((player["points"], player["assists"], player["fgm"], player["fga"],
                            player["threes"], player["ftm"], player["ftm"], row["id"]))
        changes.append((game["id"], updates))
    return changes


def main():
    parser = argparse.ArgumentParser(description="从逐回合修复可识别的旧演示赛季，默认只预览")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-count", type=int)
    args = parser.parse_args()
    if args.apply and (args.expected_count is None or args.expected_count < 1):
        parser.error("应用前请备份并指定预览得到的 --expected-count")

    with transaction() as connection, connection.cursor() as cursor:
        changes = legacy_boxscores(cursor)
        print(f"已核实的旧演示比赛: {len(changes)}")
        if not args.apply:
            return
        if args.expected_count != len(changes):
            parser.error("可修复数量与预览不同，请重新核对")
        for game_id, values in changes:
            cursor.executemany(
                """
                UPDATE player_game_stats
                SET points=%s,assists=%s,field_goals_made=%s,field_goals_attempted=%s,
                    three_points_made=%s,free_throws_made=%s,free_throws_attempted=%s
                WHERE id=%s
                """,
                values,
            )
            cursor.execute("UPDATE games SET period=4,seconds_remaining=0 WHERE id=%s", (game_id,))
        print(f"已修复 {len(changes)} 场旧演示比赛的得分、投篮与助攻。")


if __name__ == "__main__":
    main()
