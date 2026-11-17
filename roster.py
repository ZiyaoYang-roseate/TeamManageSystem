from decimal import Decimal, ROUND_HALF_UP

from db import fetch_one, transaction
from validation import BusinessError


def lock_teams(cursor, *team_ids):
    # All roster writes lock teams in ID order, followed by players in ID order.
    for team_id in sorted(set(team_ids)):
        cursor.execute("SELECT id FROM teams WHERE id = %s FOR UPDATE", (team_id,))
        if cursor.fetchone() is None:
            raise BusinessError("球队不存在", 404)


def lock_player(cursor, player_id):
    cursor.execute(
        "SELECT id, name, age, team_id, jersey_number FROM players WHERE id = %s FOR UPDATE",
        (player_id,),
    )
    player = cursor.fetchone()
    if player is None:
        raise BusinessError("球员不存在", 404)
    return player


def season_salary_sql(alias="c"):
    return f"ROUND({alias}.annual_salary * (1 + {alias}.annual_increase_pct * (%s - {alias}.start_year)))"


def contract_year_salaries(salary, years, annual_increase_pct):
    increase = Decimal(str(annual_increase_pct))
    return [int((salary * (1 + index * increase)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) for index in range(years)]


def team_payroll(team_id, season, cursor=None):
    year = int(season.split("-", 1)[0])
    sql = f"""
        SELECT COALESCE(SUM({season_salary_sql()} + incentives), 0) AS payroll
        FROM contracts c
        WHERE team_id = %s AND status = 'Active'
          AND start_year <= %s AND end_year >= %s
    """
    params = (year, team_id, year, year)
    if cursor is None:
        row = fetch_one(sql, params)
    else:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    return int(row["payroll"])


def max_contract_salary(cap_amount, player_age):
    service_years = max(0, int(player_age) - 19)
    percent = 25 if service_years <= 6 else 30 if service_years <= 9 else 35
    return int(cap_amount) * percent // 100


def available_jersey(cursor, preferred, team_id, exclude_player=None):
    sql = "SELECT jersey_number FROM players WHERE team_id = %s"
    params = (team_id,)
    if exclude_player is not None:
        sql += " AND id <> %s"
        params += (exclude_player,)
    cursor.execute(sql, params)
    used = {row["jersey_number"] for row in cursor.fetchall()}
    if preferred not in used:
        return preferred
    for candidate in range(100):
        if candidate not in used:
            return candidate
    raise BusinessError("目标球队没有可用球衣号码", 409)


def sign_free_agent(team_id, player_id, salary, years, season, *, offer_id=None, offer_version=None):
    with transaction() as connection, connection.cursor() as cursor:
        lock_teams(cursor, team_id)
        player = lock_player(cursor, player_id)
        if player["team_id"] is not None:
            raise BusinessError("该球员当前并非自由球员", 409)
        increase, contract_type, team_option, player_option = Decimal("0"), "Standard", 0, 0
        if offer_id is not None:
            cursor.execute(
                """
                SELECT o.* FROM contract_offers o
                JOIN free_agency_targets t ON t.id = o.target_id
                WHERE o.id = %s AND t.team_id = %s AND t.player_id = %s FOR UPDATE
                """,
                (offer_id, team_id, player_id),
            )
            offer = cursor.fetchone()
            if offer is None:
                raise BusinessError("本队的确认报价不存在", 404)
            if offer["status"] != "Draft" or offer["version"] != offer_version or offer["season"] != season:
                raise BusinessError("报价已变更或已使用，请重新查看并确认", 409)
            salary, years = offer["year1_salary"], offer["years"]
            increase, contract_type = offer["annual_increase_pct"], offer["exception_type"]
            team_option, player_option = offer["team_option"], offer["player_option"]
        if salary <= 0:
            raise BusinessError("正式合同年薪必须大于 0")
        cursor.execute("SELECT COUNT(*) AS total FROM players WHERE team_id = %s", (team_id,))
        if cursor.fetchone()["total"] >= 15:
            raise BusinessError("球队阵容已满 15 人，请先裁员", 409)

        cursor.execute("SELECT cap_amount, luxury_tax_line, first_apron FROM salary_caps WHERE season = %s", (season,))
        cap = cursor.fetchone()
        if cap is None:
            raise BusinessError("该赛季缺少工资帽数据")
        if salary > max_contract_salary(cap["cap_amount"], player["age"]):
            raise BusinessError("首年年薪超过该球员可签顶薪上限", 409)
        year = int(season.split("-", 1)[0])
        for index, cost in enumerate(contract_year_salaries(salary, years, increase)):
            start = year + index
            term_season = f"{start}-{(start + 1) % 100:02d}"
            term_cap = cap
            if index:
                cursor.execute("SELECT luxury_tax_line, first_apron FROM salary_caps WHERE season = %s", (term_season,))
                term_cap = cursor.fetchone()
            if term_cap is None:
                continue
            projected = team_payroll(team_id, term_season, cursor) + cost
            if projected > term_cap["luxury_tax_line"]:
                raise BusinessError(f"{term_season} 赛季签约后总薪资超过奢侈税线，无法签约", 409)
            if contract_type in {"Full MLE", "BAE"} and projected > term_cap["first_apron"]:
                raise BusinessError(f"{term_season} 赛季该特例会超过第一土豪线，无法签约", 409)

        if offer_id is None:
            # The assisted plan already displays and confirms the exact fixed terms.
            cursor.execute(
                """
                INSERT INTO free_agency_targets (team_id, player_id, tier, priority_order)
                VALUES (%s, %s, 'Vet Min', 0) ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)
                """,
                (team_id, player_id),
            )
            cursor.execute(
                """
                INSERT INTO contract_offers
                    (target_id, season, exception_type, years, year1_salary, annual_increase_pct,
                     team_option, player_option, status)
                VALUES (%s, %s, 'Standard', %s, %s, 0, 0, 0, 'Signed')
                """,
                (cursor.lastrowid, season, years, salary),
            )
            offer_id = cursor.lastrowid
        else:
            cursor.execute("UPDATE contract_offers SET status = 'Signed' WHERE id = %s", (offer_id,))

        jersey = available_jersey(cursor, player["jersey_number"], team_id)
        cursor.execute(
            "UPDATE contracts SET status = 'Waived' WHERE player_id = %s AND status = 'Active'",
            (player_id,),
        )
        cursor.execute(
            "UPDATE players SET team_id = %s, salary = %s, jersey_number = %s WHERE id = %s",
            (team_id, salary, jersey, player_id),
        )
        cursor.execute(
            """
            INSERT INTO contracts
                (player_id, team_id, start_year, end_year, annual_salary, annual_increase_pct, source_offer_id,
                 contract_type, player_option, team_option, incentives, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 'Active')
            """,
            (player_id, team_id, year, year + years - 1, salary, increase, offer_id, contract_type, player_option, team_option),
        )
        cursor.execute(
            """
            INSERT INTO free_agent_signing_history
                (player_id, team_id, player_name, season, contract_years, annual_salary, jersey_number)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (player_id, team_id, player["name"], season, years, salary, jersey),
        )


def waive_roster_player(team_id, player_id):
    with transaction() as connection, connection.cursor() as cursor:
        lock_teams(cursor, team_id)
        player = lock_player(cursor, player_id)
        if player["team_id"] != team_id:
            raise BusinessError("管理层只能裁掉自己球队的球员", 403)
        cursor.execute("UPDATE players SET team_id = NULL, salary = 0 WHERE id = %s", (player_id,))
        cursor.execute(
            "UPDATE contracts SET status = 'Waived' WHERE player_id = %s AND status = 'Active'",
            (player_id,),
        )
