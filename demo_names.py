import re


TEAM_NAMES = {
    "LAL": ("洛杉矶", "湖人", "加密货币网体育馆"),
    "BOS": ("波士顿", "凯尔特人", "TD 花园"),
    "GSW": ("金州", "勇士", "大通中心"),
    "MIA": ("迈阿密", "热火", "卡西亚中心"),
    "CHI": ("芝加哥", "公牛", "联合中心"),
    "DEN": ("丹佛", "掘金", "波尔球馆"),
    "MIL": ("密尔沃基", "雄鹿", "费哲论坛球馆"),
    "PHX": ("菲尼克斯", "太阳", "足迹中心"),
    "NYK": ("纽约", "尼克斯", "麦迪逊广场花园"),
    "BKN": ("布鲁克林", "篮网", "巴克莱中心"),
    "PHI": ("费城", "76人", "富国银行中心"),
    "TOR": ("多伦多", "猛龙", "丰业银行球馆"),
    "CLE": ("克利夫兰", "骑士", "火箭抵押贷款球馆"),
    "DET": ("底特律", "活塞", "小凯撒球馆"),
    "IND": ("印第安纳", "步行者", "甘布里吉球馆"),
    "ATL": ("亚特兰大", "老鹰", "州立农业球馆"),
    "CHA": ("夏洛特", "黄蜂", "光谱中心"),
    "WAS": ("华盛顿", "奇才", "第一资本球馆"),
    "ORL": ("奥兰多", "魔术", "起亚中心"),
    "HOU": ("休斯敦", "火箭", "丰田中心"),
    "SAS": ("圣安东尼奥", "马刺", "弗罗斯特银行中心"),
    "DAL": ("达拉斯", "独行侠", "美国航空中心"),
    "NOP": ("新奥尔良", "鹈鹕", "冰沙国王中心"),
    "MEM": ("孟菲斯", "灰熊", "联邦快递球馆"),
    "OKC": ("俄克拉荷马城", "雷霆", "佩康中心"),
    "MIN": ("明尼苏达", "森林狼", "标靶中心"),
    "POR": ("波特兰", "开拓者", "摩达中心"),
    "UTA": ("犹他", "爵士", "德尔塔中心"),
    "SAC": ("萨克拉门托", "国王", "金一中心"),
    "LAC": ("洛杉矶", "快船", "因图伊特球馆"),
}

# Stable aliases for the fictional players and coaches in the demonstration data.
GIVEN_NAMES = {
    "Aaron": "奕衡", "Andre": "景松", "Avery": "嘉旭", "Ben": "立诚", "Bill": "敬文",
    "Blake": "知行", "Brandon": "承洲", "Brayden": "远川", "Brett": "柏舟", "Caleb": "子恒",
    "Cameron": "启明", "Carter": "峻熙", "Chase": "宇航", "Chris": "鸿达", "Cody": "俊远",
    "Cole": "元朗", "Corey": "明轩", "Damon": "弘毅", "Daniel": "修远", "Darius": "少泽",
    "Dave": "志恒", "Derek": "以安", "Derrick": "沐阳", "Devin": "星河", "Dylan": "予川",
    "Elijah": "庭深", "Elliot": "延昭", "Eric": "怀礼", "Ethan": "书航", "Finn": "闻秋",
    "Frank": "明达", "Garrett": "崇岳", "Grant": "越舟", "Hunter": "牧野", "Isaiah": "云开",
    "Jalen": "景行", "Jamal": "浩然", "Jason": "逸峰", "Jaxon": "泽宇", "Jaylen": "泽楷",
    "Jeff": "敬川", "Joe": "文斌", "Jonah": "睿哲", "Jordan": "岳衡", "Kai": "昭宁",
    "Kenny": "志安", "Kobe": "卓群", "Kyle": "子昂", "Landon": "望远", "Leon": "林川",
    "Logan": "凌霄", "Luke": "俊平", "Malik": "仲扬", "Marcus": "亦帆", "Mark": "远山",
    "Mason": "知远", "Micah": "清和", "Mike": "正阳", "Miles": "立扬", "Nathaniel": "宇澄",
    "Nick": "伟辰", "Noah": "星宇", "Owen": "天朗", "Parker": "庭宇", "Quinn": "清越",
    "Ray": "振阳", "Rob": "世杰", "Ryan": "子墨", "Sam": "松年", "Sawyer": "思成",
    "Shawn": "辰轩", "Steve": "守诚", "Theo": "泊远", "Tim": "承志", "Tom": "立新",
    "Trevor": "天佑", "Trey": "明哲", "Tristan": "景程", "Tyler": "乐天", "Victor": "长胜",
    "Wesley": "文博", "Will": "承信", "Xavier": "柏霖", "Zane": "子谦",
}
FAMILY_NAMES = {
    "Adams": "陶", "Anderson": "杜", "Bailey": "孟", "Barnes": "许", "Bell": "方",
    "Bennett": "何", "Brooks": "周", "Brown": "吴", "Bryant": "陆", "Butler": "曾",
    "Carter": "沈", "Clark": "郑", "Coleman": "冯", "Collins": "谢", "Cook": "孙",
    "Evans": "蒋", "Foster": "徐", "Grant": "魏", "Green": "高", "Hayes": "顾",
    "Hill": "岳", "Howard": "章", "Hughes": "林", "James": "钱", "Johnson": "罗",
    "Kelly": "葛", "King": "王", "Lawson": "廖", "Lee": "李", "Lewis": "刘",
    "Long": "龙", "Mitchell": "程", "Moore": "莫", "Morgan": "马", "Morris": "吕",
    "Nelson": "董", "Parker": "潘", "Patterson": "石", "Price": "白", "Reed": "梁",
    "Rivera": "江", "Ross": "赵", "Russell": "韩", "Sanders": "桑", "Scott": "苏",
    "Simmons": "司马", "Stone": "史", "Turner": "唐", "Walker": "伍", "Wallace": "万",
    "Ward": "卫", "Wright": "尤", "Young": "杨",
}

_SPECIAL_NAMES = {"brunson": "布伦森"}
_TEAM_ALIASES = {
    "Lakers": "LAL", "Celtics": "BOS", "Warriors": "GSW", "Heat": "MIA", "Bulls": "CHI",
    "Nuggets": "DEN", "Bucks": "MIL", "Suns": "PHX", "Knicks": "NYK", "Nets": "BKN",
    "76ers": "PHI", "Raptors": "TOR", "Cavaliers": "CLE", "Pistons": "DET", "Pacers": "IND",
    "Hawks": "ATL", "Hornets": "CHA", "Wizards": "WAS", "Magic": "ORL", "Rockets": "HOU",
    "Spurs": "SAS", "Mavericks": "DAL", "Pelicans": "NOP", "Grizzlies": "MEM", "Thunder": "OKC",
    "Timberwolves": "MIN", "Trail Blazers": "POR", "Jazz": "UTA", "Kings": "SAC", "Clippers": "LAC",
}
_NAME_PATTERN = re.compile(
    r"(?<![A-Za-z])(?:"
    r"(?:" + "|".join(GIVEN_NAMES) + r") (?:" + "|".join(FAMILY_NAMES) + r")"
    r"|brunson|" + "|".join(_TEAM_ALIASES) + r")(?![A-Za-z])"
)


def chinese_name(name):
    if name in _SPECIAL_NAMES:
        return _SPECIAL_NAMES[name]
    first, _, last = name.partition(" ")
    if first in GIVEN_NAMES and last in FAMILY_NAMES:
        return FAMILY_NAMES[last] + GIVEN_NAMES[first]
    return name


def chinese_names_in_text(text):
    def replace(match):
        name = match.group()
        team = _TEAM_ALIASES.get(name)
        return TEAM_NAMES[team][1] if team else chinese_name(name)

    return _NAME_PATTERN.sub(replace, text)
