import argparse

import pymysql

from db import DB_CONFIG
from migrate_db import create_database, migrate, schema_statements
from seed_data import seed_demo_data, validate_demo_games


def main():
    parser = argparse.ArgumentParser(description="重建独立的演示数据库")
    parser.add_argument("--reset-demo", action="store_true")
    args = parser.parse_args()
    if not args.reset_demo or not DB_CONFIG["database"].endswith("_demo"):
        parser.error("重建演示库需要 --reset-demo，且 MYSQL_DATABASE 必须以 _demo 结尾")

    create_database()
    with pymysql.connect(**DB_CONFIG) as connection:
        with connection.cursor() as cursor:
            for statement in schema_statements():
                cursor.execute(statement)
        counts = seed_demo_data(connection)
        migrate(connection)
        checked = validate_demo_games(connection)
    print("演示库重建完成")
    for table, count in counts.items():
        print(f"  {table}: {count}")
    print(f"  已对账完赛: {checked}")


if __name__ == "__main__":
    main()
