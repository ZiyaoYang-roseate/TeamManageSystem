import itertools
import math
import random
from datetime import date, timedelta


TEAMS = [
    ("Los Angeles", "Lakers", "LAL", 1947, "Crypto.com Arena"),
    ("Boston", "Celtics", "BOS", 1946, "TD Garden"),
    ("Golden State", "Warriors", "GSW", 1946, "Chase Center"),
    ("Miami", "Heat", "MIA", 1988, "Kaseya Center"),
    ("Chicago", "Bulls", "CHI", 1966, "United Center"),
    ("Denver", "Nuggets", "DEN", 1967, "Ball Arena"),
    ("Milwaukee", "Bucks", "MIL", 1968, "Fiserv Forum"),
    ("Phoenix", "Suns", "PHX", 1968, "Footprint Center"),
    ("New York", "Knicks", "NYK", 1946, "Madison Square Garden"),
    ("Brooklyn", "Nets", "BKN", 1967, "Barclays Center"),
    ("Philadelphia", "76ers", "PHI", 1946, "Wells Fargo Center"),
    ("Toronto", "Raptors", "TOR", 1995, "Scotiabank Arena"),
    ("Cleveland", "Cavaliers", "CLE", 1970, "Rocket Mortgage FieldHouse"),
    ("Detroit", "Pistons", "DET", 1941, "Little Caesars Arena"),
    ("Indiana", "Pacers", "IND", 1967, "Gainbridge Fieldhouse"),
    ("Atlanta", "Hawks", "ATL", 1946, "State Farm Arena"),
    ("Charlotte", "Hornets", "CHA", 1988, "Spectrum Center"),
    ("Washington", "Wizards", "WAS", 1961, "Capital One Arena"),
    ("Orlando", "Magic", "ORL", 1989, "Kia Center"),
    ("Houston", "Rockets", "HOU", 1967, "Toyota Center"),
    ("San Antonio", "Spurs", "SAS", 1967, "Frost Bank Center"),
    ("Dallas", "Mavericks", "DAL", 1980, "American Airlines Center"),
    ("New Orleans", "Pelicans", "NOP", 2002, "Smoothie King Center"),
    ("Memphis", "Grizzlies", "MEM", 1995, "FedExForum"),
    ("Oklahoma City", "Thunder", "OKC", 1967, "Paycom Center"),
    ("Minnesota", "Timberwolves", "MIN", 1989, "Target Center"),
    ("Portland", "Trail Blazers", "POR", 1970, "Moda Center"),
    ("Utah", "Jazz", "UTA", 1974, "Delta Center"),
    ("Sacramento", "Kings", "SAC", 1945, "Golden 1 Center"),
    ("LA", "Clippers", "LAC", 1970, "Intuit Dome"),
]

COACH_FIRST_NAMES = [
    "Luke", "Mason", "Andre", "Tyler", "Marcus", "Jordan", "Daniel", "Chris",
    "Eric", "Steve", "Mike", "Jason", "Nick", "Joe", "Frank", "Kenny",
    "Sam", "Bill", "Aaron", "Mark", "Dave", "Jeff", "Tom", "Will",
    "Rob", "Tim", "Ben", "Ray", "Corey", "Jamal",
]

COACH_LAST_NAMES = [
    "Carter", "Brooks", "Bell", "Morgan", "Hill", "Reed", "King", "Walker",
    "Young", "Scott", "Brown", "Green", "Adams", "Lewis", "Nelson", "Cook",
    "Bailey", "Barnes", "Kelly", "Ross", "Ward", "Mitchell", "Bennett", "Parker",
    "Foster", "Collins", "Turner", "Hayes", "Simmons", "Russell",
]

PLAYER_FIRST_NAMES = [
    "Marcus", "Darius", "Jalen", "Victor", "Noah", "Elijah", "Caleb", "Isaiah",
    "Miles", "Owen", "Kai", "Avery", "Tristan", "Xavier", "Leon", "Andre",
    "Carter", "Dylan", "Malik", "Jordan", "Ryan", "Cole", "Tyler", "Cameron",
    "Nathaniel", "Brayden", "Logan", "Chase", "Parker", "Hunter", "Landon",
    "Jaxon", "Sawyer", "Micah", "Zane", "Theo", "Quinn", "Garrett", "Elliot",
    "Finn", "Devin", "Brett", "Jonah", "Trey", "Kobe", "Jaylen", "Jamal",
    "Derrick", "Shawn", "Cody", "Ethan", "Brandon", "Kyle", "Trevor", "Blake",
    "Grant", "Wesley", "Damon", "Derek",
]

PLAYER_LAST_NAMES = [
    "Lee", "Reed", "Brooks", "Stone", "Bell", "Grant", "Moore", "Clark",
    "Turner", "Price", "Johnson", "Collins", "Ward", "Morgan", "Hayes", "Young",
    "James", "Parker", "Evans", "Bennett", "Foster", "Simmons", "Wright",
    "Ross", "Cook", "Sanders", "Butler", "Rivera", "Bailey", "Coleman",
    "Hughes", "Russell", "Howard", "Morris", "Bryant", "Long", "Patterson",
    "Mitchell", "Wallace", "Anderson", "Lawson", "Price", "Walker", "Carter",
    "Nelson", "Kelly", "Barnes", "Lewis", "Adams", "Green", "Scott", "Brown",
    "Hayes", "Simmons", "Turner", "Collins", "Bennett", "Parker", "Foster",
    "Ward",
]

POSITIONS = ["PG", "SG", "SF", "PF", "C"]
ROSTER_POSITIONS = ["PG", "PG", "SG", "SG", "SF", "SF", "PF", "PF", "C", "C"]
SEASONS = [
    ("2023-24", date(2023, 10, 20)),
    ("2024-25", date(2024, 10, 20)),
    ("2025-26", date(2025, 10, 20)),
]
JERSEY_NUMBERS = [0, 1, 2, 3, 4, 5, 7, 8, 9, 10]
GAMES_PER_TEAM = 6
NATIONALITIES = [
    "USA", "USA", "USA", "Canada", "France", "Spain", "Germany",
    "Australia", "Brazil", "Greece", "Serbia", "Turkey",
]

POSITION_RANGES = {
    "PG": (183, 192, 78, 92),
    "SG": (190, 200, 84, 100),
    "SF": (198, 207, 95, 110),
    "PF": (205, 211, 104, 118),
    "C": (208, 218, 108, 128),
}


def insert_teams(connection):
    team_ids = {}
    with connection.cursor() as cursor:
        for city, name, abbreviation, founded_year, arena in TEAMS:
            cursor.execute(
                """
                INSERT INTO teams (city, name, abbreviation, founded_year, arena)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (city, name, abbreviation, founded_year, arena),
            )
            team_ids[abbreviation] = cursor.lastrowid
    connection.commit()
    return team_ids


def insert_coaches(connection, team_ids, rng):
    with connection.cursor() as cursor:
        for team_index, (_, _, abbreviation, _, _) in enumerate(TEAMS):
            name = f"{rng.choice(COACH_FIRST_NAMES)} {rng.choice(COACH_LAST_NAMES)}"
            cursor.execute(
                """
                INSERT INTO coaches (team_id, name, nationality, experience_years)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    team_ids[abbreviation],
                    name,
                    rng.choice(NATIONALITIES),
                    rng.randint(3, 16),
                ),
            )
    connection.commit()


def insert_players(connection, team_ids, rng):
    player_ids = []
    used_names = set()
    with connection.cursor() as cursor:
        for index in range(len(TEAMS) * 10):
            team_index = index // 10
            team_abbr = TEAMS[team_index][2]
            position = ROSTER_POSITIONS[index % 10]
            jersey_number = JERSEY_NUMBERS[index % 10]
            min_height, max_height, min_weight, max_weight = POSITION_RANGES[position]

            while True:
                name = f"{rng.choice(PLAYER_FIRST_NAMES)} {rng.choice(PLAYER_LAST_NAMES)}"
                if name not in used_names:
                    used_names.add(name)
                    break

            cursor.execute(
                """
                INSERT INTO players
                    (team_id, name, jersey_number, position, age, height_cm,
                     weight_kg, nationality, salary)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    team_ids[team_abbr],
                    name,
                    jersey_number,
                    position,
                    rng.randint(21, 35),
                    rng.randint(min_height, max_height),
                    rng.randint(min_weight, max_weight),
                    rng.choice(NATIONALITIES),
                    (
                        rng.randint(110, 220) * 100000
                        if team_index < 10
                        else rng.randint(5, 65) * 100000
                    ),
                ),
            )
            player_ids.append(cursor.lastrowid)
    connection.commit()
    return player_ids


def insert_free_agents(connection, rng):
    free_agents = [
        ("Brett Coleman", "SG", 0, 30, 196, 92, "USA", 8_000_000),
        ("Micah Lawson", "PF", 9, 28, 206, 108, "USA", 12_000_000),
        ("Jonah Price", "C", 15, 31, 211, 116, "Canada", 9_500_000),
        ("Trey Mitchell", "PG", 4, 26, 188, 84, "USA", 6_500_000),
        ("Devin Wallace", "SF", 22, 29, 201, 102, "USA", 11_000_000),
        ("Kobe Anderson", "SG", 12, 27, 193, 90, "Australia", 5_000_000),
    ]
    ids = []
    with connection.cursor() as cursor:
        for name, position, jersey_number, age, height_cm, weight_kg, nationality, salary in free_agents:
            cursor.execute(
                """
                INSERT INTO players
                    (team_id, name, jersey_number, position, age, height_cm,
                     weight_kg, nationality, salary)
                VALUES (NULL, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (name, jersey_number, position, age, height_cm, weight_kg, nationality, salary),
            )
            ids.append(cursor.lastrowid)
    connection.commit()
    return ids


def build_stat_line(rng, game_id, player_id, team_id, position, team_score, point_diff):
    weights = {"PG": 1.25, "SG": 1.15, "SF": 1.0, "PF": 0.9, "C": 0.8}
    weight = weights[position] * rng.uniform(0.78, 1.2)
    points = max(0, int(round(team_score * weight / 10.2)))
    points = min(points, 36)

    three_points_made = min(rng.randint(0, 5), points // 3)
    two_point_target = points - 3 * three_points_made
    free_throws_made = rng.randint(0, two_point_target)
    if (two_point_target - free_throws_made) % 2:
        free_throws_made = max(0, free_throws_made - 1)
    field_goals_made = (two_point_target - free_throws_made) // 2
    free_throws_attempted = free_throws_made + rng.randint(0, 3)

    return (
        game_id,
        player_id,
        team_id,
        rng.randint(22, 40),
        points,
        rng.randint(0, 15) + (6 if position in ("PF", "C") else 1),
        rng.randint(0, 12) + (5 if position in ("PG", "SG") else 1),
        rng.randint(0, 4),
        rng.randint(0, 4) + (1 if position in ("PF", "C") else 0),
        rng.randint(0, 5),
        rng.randint(0, 5),
        0,
        0,
        field_goals_made,
        field_goals_made + rng.randint(3, 15),
        three_points_made,
        free_throws_made,
        free_throws_attempted,
        int(point_diff * rng.uniform(0.15, 0.5)) + rng.randint(-5, 5),
    )


def build_shot_attempt(rng, game_id, player_id, team_id):
    zone_weights = [
        ("Restricted Area", 0.20),
        ("In The Paint (Non-RA)", 0.18),
        ("Mid-Range", 0.25),
        ("Left Corner 3PT", 0.11),
        ("Right Corner 3PT", 0.11),
        ("Above The Break 3PT", 0.15),
    ]
    zones = [zone for zone, _ in zone_weights]
    weights = [weight for _, weight in zone_weights]
    zone = rng.choices(zones, weights=weights, k=1)[0]

    if zone == "Restricted Area":
        x = rng.randint(-55, 55)
        y = rng.randint(0, 120)
        shot_type = rng.choice(["Dunk", "Layup"])
        made_chance = 0.68
    elif zone == "In The Paint (Non-RA)":
        x = rng.randint(-100, 100)
        y = rng.randint(130, 280)
        shot_type = rng.choice(["Layup", "Floater"])
        made_chance = 0.54
    elif zone == "Mid-Range":
        x = rng.randint(-220, 220)
        y = rng.randint(280, 390)
        shot_type = rng.choice(["Pull-up", "Floater", "Post-Up"])
        made_chance = 0.42
    elif zone == "Left Corner 3PT":
        x = rng.randint(-250, -190)
        y = rng.randint(350, 470)
        shot_type = "Corner Three"
        made_chance = 0.38
    elif zone == "Right Corner 3PT":
        x = rng.randint(190, 250)
        y = rng.randint(350, 470)
        shot_type = "Corner Three"
        made_chance = 0.38
    else:
        x = rng.randint(-180, 180)
        y = rng.randint(180, 280)
        shot_type = rng.choice(["Spot-up Three", "Pull-up Three"])
        made_chance = 0.36

    distance_cm = int(math.hypot(x, y))
    made = 1 if rng.random() < made_chance else 0
    return (
        game_id,
        player_id,
        team_id,
        rng.randint(1, 4),
        rng.randint(0, 720),
        x,
        y,
        distance_cm,
        shot_type,
        zone,
        made,
    )


def build_scoring_shot_location(rng, points):
    if points == 3:
        zone_weights = [
            ("Left Corner 3PT", 0.18),
            ("Right Corner 3PT", 0.18),
            ("Above The Break 3PT", 0.64),
        ]
    else:
        zone_weights = [
            ("Restricted Area", 0.38),
            ("In The Paint (Non-RA)", 0.34),
            ("Mid-Range", 0.28),
        ]
    zones = [zone for zone, _ in zone_weights]
    weights = [weight for _, weight in zone_weights]
    zone = rng.choices(zones, weights=weights, k=1)[0]

    if zone == "Restricted Area":
        x = rng.randint(-55, 55)
        y = rng.randint(0, 120)
        shot_type = rng.choice(["Dunk", "Layup"])
    elif zone == "In The Paint (Non-RA)":
        x = rng.randint(-100, 100)
        y = rng.randint(130, 280)
        shot_type = rng.choice(["Layup", "Floater"])
    elif zone == "Mid-Range":
        x = rng.randint(-220, 220)
        y = rng.randint(280, 390)
        shot_type = rng.choice(["Pull-up", "Floater", "Post-Up"])
    elif zone == "Left Corner 3PT":
        x = rng.randint(-250, -190)
        y = rng.randint(350, 470)
        shot_type = "Corner Three"
    elif zone == "Right Corner 3PT":
        x = rng.randint(190, 250)
        y = rng.randint(350, 470)
        shot_type = "Corner Three"
    else:
        x = rng.randint(-180, 180)
        y = rng.randint(180, 280)
        shot_type = rng.choice(["Spot-up Three", "Pull-up Three"])

    return x, y, int(math.hypot(x, y)), shot_type, zone


def score_events_for_team(rng, target_score):
    remaining = target_score
    events = []
    while remaining > 0:
        if remaining == 1:
            points = 1
        elif remaining == 2:
            points = 2
        elif remaining == 3:
            points = 3
        else:
            points = rng.choice([1, 2, 2, 3, 2, 3, 2])
        events.append(points)
        remaining -= points
    return events


def missed_shot_events_for_team(rng, team_id, team_players, count):
    events = []
    for _ in range(count):
        player_id = rng.choice(team_players[team_id])[0]
        shot_points = rng.choice([2, 2, 2, 3])
        events.append((team_id, player_id, None, -shot_points))
    return events


def insert_games_and_tracking(connection, team_ids, player_ids, rng, season, start_date):
    team_abbrs = [team[2] for team in TEAMS]
    team_id_list = [team_ids[abbr] for abbr in team_abbrs]
    team_players = {team_id: [] for team_id in team_id_list}

    with connection.cursor() as name_cursor:
        name_cursor.execute("SELECT id, name FROM players")
        id_to_name = {row["id"]: row["name"] for row in name_cursor.fetchall()}

    for index, player_id in enumerate(player_ids):
        team_abbr = TEAMS[index // 10][2]
        team_id = team_ids[team_abbr]
        team_players[team_id].append((player_id, ROSTER_POSITIONS[index % 10]))

    stats_rows = []
    shot_rows = []
    pbp_rows = []

    schedule = []
    round_robin = team_id_list[:]
    for round_index in range(GAMES_PER_TEAM):
        for i in range(len(round_robin) // 2):
            left = round_robin[i]
            right = round_robin[-1 - i]
            home_team_id = left if (round_index + i) % 2 == 0 else right
            away_team_id = right if home_team_id == left else left
            schedule.append((home_team_id, away_team_id))
        round_robin = [round_robin[0], round_robin[-1], *round_robin[1:-1]]

    with connection.cursor() as cursor:
        game_number = 0
        for home_team_id, away_team_id in schedule:

                if game_number < 45:
                    home_score = rng.randint(98, 128)
                    away_score = rng.randint(98, 128)
                    if home_score == away_score:
                        away_score += 1
                    status = "finished"
                else:
                    home_score = 0
                    away_score = 0
                    status = "scheduled"

                cursor.execute(
                    """
                    INSERT INTO games
                        (season, game_date, home_team_id, away_team_id,
                         home_score, away_score, status, period, seconds_remaining)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 1, 720)
                    """,
                    (
                        season,
                        start_date + timedelta(days=game_number),
                        home_team_id,
                        away_team_id,
                        home_score,
                        away_score,
                        status,
                    ),
                )
                game_id = cursor.lastrowid
                game_number += 1

                if status == "scheduled":
                    continue

                for team_id, team_score in (
                    (home_team_id, home_score),
                    (away_team_id, away_score),
                ):
                    opponent_score = away_score if team_id == home_team_id else home_score
                    point_diff = team_score - opponent_score
                    for player_id, position in team_players[team_id]:
                        stats_rows.append(
                            build_stat_line(
                                rng,
                                game_id,
                                player_id,
                                team_id,
                                position,
                                team_score,
                                point_diff,
                            )
                        )
                home_events = score_events_for_team(rng, home_score)
                away_events = score_events_for_team(rng, away_score)
                event_stream = []
                for points in home_events:
                    player_id = rng.choice(team_players[home_team_id])[0]
                    assist_player_id = (
                        rng.choice(team_players[home_team_id])[0]
                        if points in (2, 3) and rng.random() < 0.55
                        else None
                    )
                    event_stream.append((home_team_id, player_id, assist_player_id, points))
                for points in away_events:
                    player_id = rng.choice(team_players[away_team_id])[0]
                    assist_player_id = (
                        rng.choice(team_players[away_team_id])[0]
                        if points in (2, 3) and rng.random() < 0.55
                        else None
                    )
                    event_stream.append((away_team_id, player_id, assist_player_id, points))

                event_stream.extend(
                    missed_shot_events_for_team(
                        rng,
                        home_team_id,
                        team_players,
                        rng.randint(32, 48),
                    )
                )
                event_stream.extend(
                    missed_shot_events_for_team(
                        rng,
                        away_team_id,
                        team_players,
                        rng.randint(32, 48),
                    )
                )

                random.shuffle(event_stream)

                time_slots = []
                for period in range(1, 5):
                    for seconds in range(720, 0, -6):
                        time_slots.append((period, seconds))

                current_home_score = 0
                current_away_score = 0
                for event_order, (team_id, player_id, assist_player_id, raw_points) in enumerate(
                    event_stream
                ):
                    is_miss = raw_points < 0
                    points = abs(raw_points)
                    period, seconds_remaining = time_slots[event_order]
                    if raw_points > 0:
                        if team_id == home_team_id:
                            current_home_score += points
                        else:
                            current_away_score += points

                    if is_miss:
                        event_type = "Missed Shot"
                    elif points == 1:
                        event_type = "Free Throw Made"
                    elif points == 2:
                        event_type = "Two-Point Made"
                    else:
                        event_type = "Three-Point Made"

                    description = f"{id_to_name[player_id]} {event_type}"
                    if assist_player_id:
                        description += f" (assist: {id_to_name[assist_player_id]})"

                    if points in (2, 3):
                        shot_x, shot_y, shot_distance, shot_type, shot_zone = (
                            build_scoring_shot_location(rng, points)
                        )
                        shot_rows.append(
                            (
                                game_id,
                                player_id,
                                team_id,
                                period,
                                seconds_remaining,
                                shot_x,
                                shot_y,
                                shot_distance,
                                shot_type,
                                shot_zone,
                                0 if is_miss else 1,
                            )
                        )

                    pbp_rows.append(
                        (
                            game_id,
                            period,
                            seconds_remaining,
                            team_id,
                            player_id,
                            assist_player_id,
                            event_type,
                            description,
                            current_home_score,
                            current_away_score,
                        )
                    )

        cursor.executemany(
            """
            INSERT INTO player_game_stats
                (game_id, player_id, team_id, minutes_played, points, rebounds,
                 assists, steals, blocks, turnovers, fouls,
                 seconds_played, is_on_court, field_goals_made, field_goals_attempted, three_points_made,
                 free_throws_made, free_throws_attempted, plus_minus)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            stats_rows,
        )
        cursor.executemany(
            """
            INSERT INTO shot_attempts
                (game_id, player_id, team_id, period, seconds_remaining,
                 x, y, distance_cm, shot_type, zone, made)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            shot_rows,
        )
        cursor.executemany(
            """
            INSERT INTO play_by_play
                (game_id, period, seconds_remaining, team_id, player_id,
                 assist_player_id, event_type, description, score_home, score_away)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            pbp_rows,
        )
    connection.commit()
    return {
        "games": game_number,
        "player_game_stats": len(stats_rows),
        "shot_attempts": len(shot_rows),
        "play_by_play": len(pbp_rows),
    }


def insert_lineup_combinations(connection, team_ids, player_ids, rng):
    count = 0
    with connection.cursor() as cursor:
        for team_index, (_, _, abbreviation, _, _) in enumerate(TEAMS):
            team_id = team_ids[abbreviation]
            roster = player_ids[team_index * 10:(team_index + 1) * 10]
            for combination_size in (2, 3, 5):
                for combo in itertools.combinations(roster, combination_size):
                    minutes_played = rng.randint(90, 500)
                    possessions = int(minutes_played * rng.uniform(1.8, 2.3))
                    points_for = rng.randint(90, 620)
                    points_against = rng.randint(90, 620)
                    off_rating = round(points_for / possessions * 100, 2)
                    def_rating = round(points_against / possessions * 100, 2)
                    net_rating = round(off_rating - def_rating, 2)
                    cursor.execute(
                        """
                        INSERT INTO lineup_combinations
                            (team_id, combination_size, player_ids, minutes_played,
                             possessions, points_for, points_against, assists,
                             turnovers, off_rating, def_rating, net_rating)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            team_id,
                            combination_size,
                            ",".join(str(player_id) for player_id in combo),
                            minutes_played,
                            possessions,
                            points_for,
                            points_against,
                            rng.randint(20, 120),
                            rng.randint(8, 60),
                            off_rating,
                            def_rating,
                            net_rating,
                        ),
                    )
                    count += 1
    connection.commit()
    return count


def insert_salary_caps(connection):
    with connection.cursor() as cursor:
        for index, (season, _) in enumerate(SEASONS):
            cursor.execute(
                """
                INSERT INTO salary_caps
                    (season, cap_amount, luxury_tax_line, first_apron, second_apron)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    season,
                    136000000 + index * 2500000,
                    165000000 + index * 3000000,
                    172000000 + index * 3500000,
                    183000000 + index * 3500000,
                ),
            )
    connection.commit()


def insert_contracts(connection, team_ids, player_ids, rng):
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, salary FROM players ORDER BY id")
        salary_map = {row["id"]: row["salary"] for row in cursor.fetchall()}

        for index, player_id in enumerate(player_ids):
            team_abbr = TEAMS[index // 10][2]
            annual_salary = salary_map[player_id]
            if index < 8:
                contract_type = "Rookie"
            elif index % 9 == 0:
                contract_type = "Two-Way"
            else:
                contract_type = "Standard"

            cursor.execute(
                """
                INSERT INTO contracts
                    (player_id, team_id, start_year, end_year, annual_salary,
                     contract_type, player_option, team_option, incentives)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    player_id,
                    team_ids[team_abbr],
                    2025,
                    rng.choice([2027, 2028, 2029, 2030]),
                    annual_salary,
                    contract_type,
                    1 if rng.random() < 0.35 else 0,
                    1 if rng.random() < 0.35 else 0,
                    rng.randint(0, 300) * 10000,
                ),
            )
    connection.commit()


def insert_player_health(connection, player_ids, rng):
    count = 0
    statuses = ["Healthy", "Healthy", "Healthy", "Probable", "Day-to-Day", "Questionable", "Out"]
    with connection.cursor() as cursor:
        for player_id in player_ids:
            status = rng.choice(statuses)
            consecutive_games = rng.randint(0, 9)
            avg_minutes = round(rng.uniform(20, 38), 1)
            fatigue_score = int(consecutive_games * 6 + max(0, avg_minutes - 32) * 5)
            if status == "Out":
                description = rng.choice(["Left ankle sprain", "Right knee soreness", "Back spasms"])
            elif status != "Healthy":
                description = rng.choice(["Minor hamstring tightness", "Foot contusion", "Illness"])
            else:
                description = "No current injury"

            if status == "Out" or fatigue_score >= 70:
                warning = "High Load"
            elif status != "Healthy" or fatigue_score >= 50:
                warning = "Monitor"
            else:
                warning = "Normal"

            cursor.execute(
                """
                INSERT INTO player_health
                    (player_id, injury_status, injury_description, consecutive_games,
                     avg_minutes, fatigue_score, load_warning)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    player_id,
                    status,
                    description,
                    consecutive_games,
                    avg_minutes,
                    fatigue_score,
                    warning,
                ),
            )
            count += 1
    connection.commit()
    return count


def insert_scout_reports(connection, player_ids, rng):
    count = 0
    with connection.cursor() as cursor:
        for index, player_id in enumerate(player_ids):
            position = ROSTER_POSITIONS[index % 10]
            base_scores = {
                "PG": (72, 68, 90, 82, 84),
                "SG": (88, 70, 68, 84, 74),
                "SF": (78, 78, 74, 88, 78),
                "PF": (76, 86, 64, 86, 72),
                "C": (72, 90, 58, 80, 76),
            }[position]
            scoring, defense, playmaking, athleticism, basketball_iq = (
                min(99, value + rng.randint(-7, 7)) for value in base_scores
            )
            overall_rating = round(
                (scoring + defense + playmaking + athleticism + basketball_iq) / 5
            )
            notes = f"{position} prospect with balanced two-way potential and room to improve shot selection."
            cursor.execute(
                """
                INSERT INTO scout_reports
                    (player_id, scoring, defense, playmaking, athleticism,
                     basketball_iq, overall_rating, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    player_id,
                    scoring,
                    defense,
                    playmaking,
                    athleticism,
                    basketball_iq,
                    overall_rating,
                    notes,
                ),
            )
            count += 1
    connection.commit()
    return count


def insert_draft_picks(connection, team_ids, rng):
    count = 0
    with connection.cursor() as cursor:
        for team_id in team_ids.values():
            for season in ("2026", "2027", "2028"):
                first_protection = rng.choice(["Unprotected", "Lottery Protected", "Top 4 Protected"])
                first_status = rng.choice(["Own", "Own", "Traded"])
                cursor.execute(
                    """
                    INSERT INTO draft_picks
                        (team_id, season, round, pick_number, protection, status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (team_id, season, 1, rng.randint(1, 30), first_protection, first_status),
                )
                cursor.execute(
                    """
                    INSERT INTO draft_picks
                        (team_id, season, round, pick_number, protection, status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        team_id,
                        season,
                        2,
                        rng.randint(31, 60),
                        rng.choice(["Unprotected", "Top 55 Protected"]),
                        rng.choice(["Own", "Traded"]),
                    ),
                )
                count += 2
    connection.commit()
    return count


def insert_free_agency_data(connection, team_ids, player_ids, rng):
    tiers = ["Tier 1", "MLE Target", "Vet Min"]
    scouting_team = next(iter(team_ids.values()))
    chosen_player_ids = player_ids[::3][:12]
    target_ids = []

    with connection.cursor() as cursor:
        for index, player_id in enumerate(chosen_player_ids):
            tier = tiers[index % len(tiers)]
            cursor.execute(
                """
                INSERT INTO free_agency_targets
                    (team_id, player_id, tier, priority_order)
                VALUES (%s, %s, %s, %s)
                """,
                (scouting_team, player_id, tier, index),
            )
            target_ids.append(cursor.lastrowid)

        for index, target_id in enumerate(target_ids[:8]):
            years = rng.randint(1, 4)
            salary = rng.randint(12, 360) * 100000
            cursor.execute(
                """
                INSERT INTO contract_offers
                    (target_id, years, year1_salary, annual_increase_pct,
                     team_option, player_option, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    target_id,
                    years,
                    salary,
                    rng.choice([0.05, 0.08]),
                    1 if rng.random() < 0.5 else 0,
                    1 if rng.random() < 0.5 else 0,
                    "Draft",
                ),
            )
    connection.commit()
    return len(target_ids)


def seed_demo_data(connection):
    rng = random.Random(42)
    team_ids = insert_teams(connection)
    insert_coaches(connection, team_ids, rng)
    player_ids = insert_players(connection, team_ids, rng)
    free_agent_ids = insert_free_agents(connection, rng)
    tracking_counts = {
        "games": 0,
        "player_game_stats": 0,
        "shot_attempts": 0,
        "play_by_play": 0,
    }
    for season, start_date in SEASONS:
        counts = insert_games_and_tracking(
            connection,
            team_ids,
            player_ids,
            rng,
            season,
            start_date,
        )
        for key in tracking_counts:
            tracking_counts[key] += counts[key]

    tracking_counts["lineup_combinations"] = insert_lineup_combinations(
        connection, team_ids, player_ids, rng
    )
    insert_salary_caps(connection)
    insert_contracts(connection, team_ids, player_ids, rng)
    tracking_counts["player_health"] = insert_player_health(connection, player_ids, rng)
    tracking_counts["scout_reports"] = insert_scout_reports(connection, player_ids, rng)
    tracking_counts["draft_picks"] = insert_draft_picks(connection, team_ids, rng)
    tracking_counts["free_agency_targets"] = insert_free_agency_data(
        connection, team_ids, free_agent_ids, rng
    )

    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS total FROM teams")
        tracking_counts["teams"] = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) AS total FROM coaches")
        tracking_counts["coaches"] = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) AS total FROM players")
        tracking_counts["players"] = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) AS total FROM contracts")
        tracking_counts["contracts"] = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) AS total FROM salary_caps")
        tracking_counts["salary_caps"] = cursor.fetchone()["total"]

    return tracking_counts
