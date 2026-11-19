def ordered_games(games, business_date):
    def key(game):
        current = game["game_date"] == business_date and game["status"] != "cancelled"
        future = game["status"] in {"scheduled", "live"}
        group = 0 if current else 1 if future else 2 if game["status"] == "finished" else 3
        date_key = game["game_date"].toordinal() if group < 2 else -game["game_date"].toordinal()
        state = {"live": 0, "scheduled": 1, "finished": 2, "cancelled": 3}.get(game["status"], 4)
        return group, date_key, state, game["id"]
    return sorted(games, key=key)
