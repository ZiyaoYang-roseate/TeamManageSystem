import os
from contextlib import contextmanager

import pymysql
from pymysql.cursors import DictCursor

try:
    from dbutils.pooled_db import PooledDB
except ImportError:
    PooledDB = None


DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "nba_app"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "nba_management_db"),
    "charset": "utf8mb4",
    "cursorclass": DictCursor,
}


if PooledDB is not None:
    _POOL = PooledDB(
        creator=pymysql,
        maxconnections=10,
        mincached=0,
        maxcached=5,
        blocking=True,
        **DB_CONFIG,
    )
else:
    _POOL = None


def get_connection():
    if _POOL is not None:
        return _POOL.connection()
    return pymysql.connect(**DB_CONFIG)


@contextmanager
def transaction():
    connection = get_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def fetch_all(sql, params=None):
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params or ())
            return cursor.fetchall()
    finally:
        connection.close()


def fetch_one(sql, params=None):
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params or ())
            return cursor.fetchone()
    finally:
        connection.close()


def execute(sql, params=None):
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params or ())
        connection.commit()
    finally:
        connection.close()
