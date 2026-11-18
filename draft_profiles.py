from validation import BusinessError, choice


LEGACY_DEMO_SOURCE = "课程演示 · 模拟新秀比赛"
PROFILE_FIELDS = {
    "strength": ("技术特点", 105),
    "role": ("上场用法", 105),
    "risk": ("适应风险", 105),
    "test": ("试训验证", 105),
}
EXTERNAL_LEVELS = ("大学联赛", "青年联赛", "海外职业联赛", "其他外部赛事")

# Fictional schools, competitions and scouting notes for the course demo.
# The numbers are generated outside the simulated league's games database.
DEMO_PROSPECTS = [
    ("周望", "PG", 14, 28, 6.8, 2.4, 3.1, 1.2, "河湾理工大学", "大学联赛", "2025–26 校际联赛",
     "主控，擅长利用掩护找顺下和弱侧射手。十四场样本里场均 6.8 助攻、2.4 失误。",
     "给替补阵容安排高位挡拆，让他在主控休息时组织第一波进攻。",
     "面对夹击时会收球。校际样本不足以判断他能否应对更快的防守轮转。",
     "做 12 个双人夹击挡拆回合，记录有效出球、活球失误和队友空位出手。"),
    ("顾言", "PG", 13, 23, 4.3, 0.9, 3.5, 0.9, "北原大学", "大学联赛", "2025–26 校际联赛",
     "替补控卫，场均 4.3 助攻和 0.9 失误，常接应推进并在第一拍转移球。",
     "在收官前替主控分担推进和受压出球，优先搭配能自主终结的侧翼。",
     "无球投射威胁尚不明确。低失误也可能来自较少的复杂持球任务。",
     "安排全场压迫和半场夹击各一组，比较推进耗时与受压出球失误。"),
    ("林启", "SG", 12, 24, 2.1, 1.3, 3.4, 2.8, "临江师范大学", "大学联赛", "2025–26 校际联赛",
     "场均命中 2.8 记三分，多数来自定点接球和弱侧绕出；命中数不能单独说明命中率。",
     "搭配突破型主攻手站弱侧，底线绕出后接球投篮，逼对手跟防。",
     "被扑防时缺少稳定的第二动作，换到更远的三分线后还需重新测准。",
     "分底角和弧顶记录接球命中率，再增加 10 个被扑后的突破或转移球回合。"),
    ("严拓", "SG", 15, 26, 3.6, 1.8, 4.1, 1.1, "松岭大学", "大学联赛", "2025–26 校际联赛",
     "偏持球的侧翼，能攻击扑防，再把球分给底角。场均 3.6 助攻。",
     "安排他在第二侧接球后突破吸引补防，和无球射手形成互补。",
     "场均仅命中 1.1 记三分，无球威胁和绕掩护追防仍需素材。",
     "打 12 个第二侧接球的实战回合，记录突破后创造空位和防守回追。"),
    ("陈越", "SF", 13, 27, 3.2, 1.8, 5.2, 1.7, "海岬科技大学", "大学联赛", "2025–26 校际联赛",
     "能抢下篮板直接推进，落阵地后会沿底线切入，场均 5.2 篮板和 3.2 助攻。",
     "作为第二阵容转换发起点，阵地战安排弱侧切入而非长时间持球。",
     "样本中转换机会较多，半场换防压力下的对抗终结尚不稳定。",
     "对比快攻与半场终结，在有护筐者的 12 次切入中记录出手选择。"),
    ("邵辰", "SF", 10, 25, 1.6, 1.1, 5.8, 2.0, "海州青年队", "青年联赛", "2025 青年邀请赛",
     "底角接球投篮和侧翼轮转换防是主要任务，场均 2.0 记三分、5.8 个篮板。",
     "与持球核心同场填弱侧底角，并试着接防对方第二得分点。",
     "青年赛事的体型差异较大，需要检验对抗成年锋线时的站位与卡位。",
     "安排更高大的侧翼对位，记录被突破、卡位成功以及回合后的底角出手。"),
    ("许澈", "PF", 11, 25, 1.5, 1.4, 8.2, 0.7, "云岭交通大学", "大学联赛", "2025–26 校际联赛",
     "弱侧冲抢和卡位积极，场均 8.2 个篮板。主要在小阵容里打四号位。",
     "让他在替补时段补弱侧卡位，帮助球队结束防守回合。",
     "冲抢会占用退防时间，还需要看他面对高大内线时能否卡住第一位置。",
     "用大个子对手打 15 个挡拆终结回合，记录防守篮板、卡位和回防到位。"),
    ("段可", "PF", 14, 27, 2.8, 1.2, 5.6, 2.4, "赤湾青年队", "青年联赛", "2025 青年邀请赛",
     "掩护后能外弹投篮或在高位做二次传球，场均 2.4 记三分、2.8 次助攻。",
     "给突破型后卫做掩护外弹，逼对方大个子离筐，并从高位转移到弱侧。",
     "面对成年内线时的防守对抗和卡位需要验证，不宜单靠投射数据判断换防能力。",
     "混合挡拆外弹和低位对抗，记录接球投篮、二次传球及防守篮板落点。"),
    ("秦衡", "C", 14, 26, 1.8, 2.0, 9.6, 0.2, "白沙工学院", "大学联赛", "2025–26 校际联赛",
     "以掩护顺下和保护防守篮板为主，场均 9.6 个篮板。",
     "放进第二阵容做高位掩护和篮下终结，优先用在需要守住防守篮板的对局。",
     "对手拉到五外后，外扩速度和回到篮下的时机仍需检验。",
     "做沉退和外扩两组挡拆防守，记录二次进攻、对方空位三分和顺下终结。"),
    ("陆行", "PG", 2, 16, 5.0, 1.5, 2.0, 1.0, "东港青年队", "青年联赛", "2026 青年资格赛",
     "两场替补样本观察到提速与早出球，场均 5.0 助攻可能受单场影响。",
     "先试任第三控卫，在短轮换中观察能否维持节奏与传球质量。",
     "总样本约 32 分钟，无法得出稳定投射或组织结论。",
     "先补采三场不同对手的比赛，试训另加全场压迫和半场挡拆各一组。"),
]


def source_label(program, level, tournament, reference, simulated=False):
    prefix = "课程模拟" if simulated else "外部样本"
    return f"{prefix} · {level} · {program} · {tournament} · {reference}"


def observation_text(fields):
    return "\n".join(f"{label}：{fields[key]}" for key, (label, _) in PROFILE_FIELDS.items())


def _demo_rows():
    for name, position, games, minutes, assists, turnovers, rebounds, threes, program, level, tournament, *notes in DEMO_PROSPECTS:
        source = source_label(program, level, tournament, "赛季球探档案", simulated=True)
        yield (
            name, position, source, games, minutes, assists, turnovers, rebounds, threes,
            observation_text(dict(zip(PROFILE_FIELDS, notes))),
        )


DEMO_ROWS = tuple(_demo_rows())
_DEMO_BY_NAME = {row[0]: row for row in DEMO_ROWS}


def parse_profile_fields(form):
    program, tournament, reference = (form.get(key, "").strip() for key in ("external_program", "external_event", "external_reference"))
    if not all(1 <= len(value) <= limit and len(value.splitlines()) == 1 for value, limit in zip(
        (program, tournament, reference), (30, 30, 55)
    )):
        raise BusinessError("请填写外部球队或学校、赛事赛季和来源出处")
    level = choice(form.get("external_level"), "外部赛事类型", EXTERNAL_LEVELS)
    fields = {key: form.get(key, "").strip() for key in PROFILE_FIELDS}
    if any(
        not 1 <= len(fields[key]) <= limit or len(fields[key].splitlines()) != 1
        for key, (_, limit) in PROFILE_FIELDS.items()
    ):
        raise BusinessError("请分别填写技术特点、上场用法、适应风险和试训验证，每项使用单段文字")
    source = source_label(program, level, tournament, reference, simulated=form.get("simulated") == "1")
    if len(source) > 120:
        raise BusinessError("样本来源过长，请缩短赛事名称或来源出处")
    return source, observation_text(fields)


def external_profile(row):
    source, text = row["source"], row["observation"]
    # Enrich the previously seeded demo records in place by identity. IDs and saved team notes stay valid.
    old = _DEMO_BY_NAME.get(row["name"])
    if row["draft_year"] == 2026 and source == LEGACY_DEMO_SOURCE and old:
        source, text = old[2], old[-1]
    prefixes = {label: key for key, (label, _) in PROFILE_FIELDS.items()}
    fields = {}
    for line in text.splitlines():
        label, sep, value = line.partition("：")
        if sep and label in prefixes and value:
            fields[prefixes[label]] = value
    has_source = source.startswith(("课程模拟 · ", "外部样本 · "))
    return {
        "source": source,
        "external": has_source and len(fields) == len(PROFILE_FIELDS),
        "simulated": source.startswith("课程模拟 · "),
        "fields": fields,
        "legacy_note": text if not fields else "",
    }
