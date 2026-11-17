import math

from flask import request

from constants import POSITIONS, SEASONS


class ValidationError(ValueError):
    pass


class BusinessError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def json_object():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise BusinessError("请求必须包含 JSON 对象")
    return payload


def bounded_int(value, field, minimum=1, maximum=2_147_483_647):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise BusinessError(f"{field}必须是整数")
    try:
        number = int(value)
    except ValueError as exc:
        raise BusinessError(f"{field}必须是整数") from exc
    if not minimum <= number <= maximum:
        raise BusinessError(f"{field}必须在 {minimum} 和 {maximum} 之间")
    return number


def bounded_float(value, field, minimum, maximum):
    if isinstance(value, bool):
        raise BusinessError(f"{field}必须是数字")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BusinessError(f"{field}必须是数字") from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise BusinessError(f"{field}必须在 {minimum} 和 {maximum} 之间")
    return number


def choice(value, field, options):
    if not isinstance(value, str) or value not in options:
        raise BusinessError(f"无效的{field}")
    return value


def season_value(value):
    return choice(value, "赛季", SEASONS)


def parse_player_form(form):
    name = form.get("name", "").strip()
    nationality = form.get("nationality", "").strip()
    position = form.get("position", "").strip()

    if not name:
        raise ValidationError("姓名不能为空")
    if position not in POSITIONS:
        raise ValidationError("请选择有效的位置")
    if not nationality:
        raise ValidationError("国籍不能为空")

    try:
        jersey_number = int(form.get("jersey_number", ""))
        age = int(form.get("age", ""))
        height_cm = float(form.get("height_cm", ""))
        weight_kg = float(form.get("weight_kg", ""))
    except (TypeError, ValueError):
        raise ValidationError("请检查数字字段是否填写正确")

    if not 0 <= jersey_number <= 99:
        raise ValidationError("球衣号码必须在 0 到 99 之间")
    if not 18 <= age <= 45:
        raise ValidationError("年龄必须在 18 到 45 之间")
    if not 170 <= height_cm <= 235:
        raise ValidationError("身高必须在 170 到 235 厘米之间")
    if not 60 <= weight_kg <= 160:
        raise ValidationError("体重必须在 60 到 160 公斤之间")
    if len(name) > 80 or len(nationality) > 50:
        raise ValidationError("姓名或国籍超出长度限制")

    return {
        "name": name,
        "jersey_number": jersey_number,
        "position": position,
        "age": age,
        "height_cm": height_cm,
        "weight_kg": weight_kg,
        "nationality": nationality,
    }


def parse_team_form(form):
    city = form.get("city", "").strip()
    name = form.get("name", "").strip()
    abbreviation = form.get("abbreviation", "").strip().upper()
    arena = form.get("arena", "").strip()

    if not city:
        raise ValidationError("城市不能为空")
    if not name:
        raise ValidationError("球队名称不能为空")
    if len(abbreviation) != 3:
        raise ValidationError("球队缩写必须是 3 个字母")
    if not arena:
        raise ValidationError("主场球馆不能为空")
    if len(city) > 50 or len(name) > 80 or len(arena) > 100:
        raise ValidationError("城市、球队名称或球馆超出长度限制")

    try:
        founded_year = int(form.get("founded_year", ""))
    except ValueError:
        raise ValidationError("成立年份必须是数字")

    if not 1900 <= founded_year <= 2030:
        raise ValidationError("成立年份必须在 1900 到 2030 之间")

    return city, name, abbreviation, founded_year, arena
