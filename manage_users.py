import argparse
from getpass import getpass

from werkzeug.security import generate_password_hash

from auth import ROLE_LABELS, ROLE_MANAGEMENT
from db import transaction


def main():
    parser = argparse.ArgumentParser(description="创建账号或重置已有账号密码")
    parser.add_argument("username")
    parser.add_argument("--role", choices=ROLE_LABELS)
    parser.add_argument("--team", type=int, help="球队管理层所属的球队 ID")
    parser.add_argument("--display-name")
    parser.add_argument("--reset-password", action="store_true")
    args = parser.parse_args()
    if not 1 <= len(args.username) <= 50:
        parser.error("用户名长度必须为 1 到 50 个字符")
    if args.reset_password:
        if args.role or args.team is not None or args.display_name is not None:
            parser.error("重置密码时只需要用户名和 --reset-password")
    else:
        if not args.role or (args.role == ROLE_MANAGEMENT) != (args.team is not None):
            parser.error("新账号须指定角色，球队管理层还须指定 --team")
        if args.display_name is not None and not 1 <= len(args.display_name) <= 50:
            parser.error("显示名长度必须为 1 到 50 个字符")

    password = getpass("输入新密码: ")
    if len(password) < 12 or password != getpass("确认新密码: "):
        parser.error("密码至少需要 12 个字符，两次输入须一致")
    password_hash = generate_password_hash(password)
    with transaction() as connection, connection.cursor() as cursor:
        if args.reset_password:
            cursor.execute("UPDATE users SET password_hash = %s WHERE username = %s", (password_hash, args.username))
            if not cursor.rowcount:
                parser.error("账号不存在")
        else:
            cursor.execute("SELECT 1 FROM users WHERE username = %s", (args.username,))
            if cursor.fetchone():
                parser.error("账号已存在，使用 --reset-password 重置密码")
            if args.team is not None:
                cursor.execute("SELECT id FROM teams WHERE id = %s AND archived_at IS NULL FOR UPDATE", (args.team,))
                if not cursor.fetchone():
                    parser.error("球队不存在")
                cursor.execute("SELECT username FROM users WHERE team_id = %s", (args.team,))
                if cursor.fetchone():
                    parser.error("球队已关联管理账号")
            cursor.execute(
                "INSERT INTO users (username, password_hash, role, team_id, display_name) VALUES (%s, %s, %s, %s, %s)",
                (args.username, password_hash, args.role, args.team, args.display_name or args.username),
            )
    print("账号已保存")


if __name__ == "__main__":
    main()
