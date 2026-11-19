"""Create missing login accounts for an explicitly selected local demo database."""
import argparse
import re

from werkzeug.security import generate_password_hash

from db import DB_CONFIG, transaction


DEMO_PASSWORD = "123456"


def account_plan(teams, users):
    requests = [
        {"username":"league_admin","role":"league_admin","team_id":None,"team_name":"全联盟","display_name":"联赛管理员"},
        {"username":"scorekeeper","role":"scorekeeper","team_id":None,"team_name":"比赛计分","display_name":"记分员"},
    ]
    for team in teams:
        suffix = re.sub(r"[^a-z0-9]+", "", team["abbreviation"].lower()) or f"team_{team['id']}"
        requests.append({"username":f"gm_{suffix}","role":"management","team_id":team["id"],
                         "team_name":team["name"],"display_name":(team["name"]+"管理层")[:50]})
    reserved = {user["username"] for user in users}
    for entry in requests:
        existing = next((user for user in users if user["role"]==entry["role"] and user["team_id"]==entry["team_id"]),None)
        entry["created"] = existing is None
        if existing:
            entry["username"] = existing["username"]
        else:
            if entry["username"] in reserved:
                raise ValueError(f"用户名 {entry['username']} 已用于其他身份，请先处理重名账号后重试；本次不写入账号。")
            reserved.add(entry["username"])
    return requests


def initialize_demo_accounts():
    if DB_CONFIG["host"] not in {"127.0.0.1","localhost","::1"} or not DB_CONFIG["database"].endswith("_demo"):
        raise ValueError("演示账号初始化仅支持本机且库名以 _demo 结尾的数据库。")
    with transaction() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id,name,abbreviation FROM teams WHERE archived_at IS NULL ORDER BY id FOR UPDATE")
        teams = cursor.fetchall()
        cursor.execute("SELECT id,username,role,team_id FROM users ORDER BY id FOR UPDATE")
        plan = account_plan(teams,cursor.fetchall())
        password_hash = generate_password_hash(DEMO_PASSWORD)
        for entry in plan:
            if entry["created"]:
                cursor.execute(
                    "INSERT INTO users(username,password_hash,role,team_id,display_name) VALUES (%s,%s,%s,%s,%s)",
                    (entry["username"],password_hash,entry["role"],entry["team_id"],entry["display_name"]),
                )
    return plan


def main():
    parser=argparse.ArgumentParser(description="为本机 _demo 库补齐联赛管理员、记分员及每支球队的演示账号")
    parser.add_argument("--create-missing",action="store_true",help="明确创建缺少的演示账号；已有账号及其密码保留")
    args=parser.parse_args()
    if not args.create_missing:
        parser.error("请使用 --create-missing；新建演示账号的密码为 123456，仅用于本地课程演示。")
    try:
        plan=initialize_demo_accounts()
    except ValueError as exc:
        parser.error(str(exc))
    labels={"league_admin":"联赛管理员","management":"球队管理层","scorekeeper":"记分员"}
    print(f"演示账号初始化完成：新建 {sum(row['created'] for row in plan)} 个，保留 {sum(not row['created'] for row in plan)} 个。")
    print("以下新建账号的密码为 123456；标记为保留的账号使用原密码。")
    for entry in plan:
        print(f"{labels[entry['role']]} | {entry['team_name']} | {entry['username']} | {'新建：123456' if entry['created'] else '保留：使用原密码'}")


if __name__=="__main__":
    main()
