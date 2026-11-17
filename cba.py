from db import fetch_one
from roster import max_contract_salary, team_payroll


def calculate_luxury_tax(amount_over_tax):
    if amount_over_tax <= 0:
        return 0.0

    tax = 0.0
    remaining = amount_over_tax
    brackets = [
        (0, 5_000_000, 1.50),
        (5_000_000, 10_000_000, 1.75),
        (10_000_000, 15_000_000, 2.50),
        (15_000_000, 20_000_000, 3.25),
    ]

    for lower, upper, rate in brackets:
        if remaining <= 0:
            break
        taxable = min(remaining, upper - lower)
        tax += taxable * rate
        remaining -= taxable

    if remaining > 0:
        extra_brackets = max(0, int((remaining - 1) // 5_000_000))
        tax += remaining * (3.75 + extra_brackets * 0.5)

    return round(tax, 2)


def validate_and_calculate_offer(team_id, player_id, offer_data, cursor=None):
    errors = []
    warnings = []

    season = offer_data.get("season", "2025-26")
    year1_salary = int(offer_data.get("year1_salary", 0))
    exception_type = offer_data.get("exception_type", "Standard")

    def query(sql, params):
        if cursor is None:
            return fetch_one(sql, params)
        cursor.execute(sql, params)
        return cursor.fetchone()

    cap = query("SELECT * FROM salary_caps WHERE season = %s", (season,))
    player = query("SELECT age FROM players WHERE id = %s", (player_id,))

    if cap is None:
        return {
            "is_valid": False,
            "errors": ["工资帽数据不存在"],
            "warnings": [],
            "estimated_tax_penalty": 0,
        }

    if player is None:
        return {
            "is_valid": False,
            "errors": ["球员不存在"],
            "warnings": [],
            "estimated_tax_penalty": 0,
        }

    max_salary = max_contract_salary(cap["cap_amount"], player["age"])

    if year1_salary > max_salary:
        errors.append(
            f"首年年薪超过该球员可签顶薪上限 ${max_salary:,}"
        )

    current_payroll = team_payroll(team_id, season, cursor)
    projected_payroll = current_payroll + year1_salary

    if exception_type in ("Full MLE", "BAE") and projected_payroll > cap["first_apron"]:
        errors.append(
            "违反 CBA 规则：使用该特例将触发硬工资帽，总薪资不能超过第一土豪线"
        )

    if projected_payroll > cap["second_apron"]:
        warnings.append("签约后总薪资超过第二土豪线，球队将失去迷你中产并限制交易")
    elif projected_payroll > cap["first_apron"]:
        warnings.append("签约后总薪资超过第一土豪线，将触发硬工资帽限制")

    tax_amount = projected_payroll - cap["luxury_tax_line"]
    estimated_tax_penalty = calculate_luxury_tax(tax_amount)

    return {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "estimated_tax_penalty": estimated_tax_penalty,
        "projected_payroll": projected_payroll,
        "max_salary": max_salary,
    }
