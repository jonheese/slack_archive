"""One connection per request; bounded lifetime even when queries fail."""
from time import perf_counter
import mysql.connector
from flask import current_app, g


def connection():
    if 'db' not in g:
        config = current_app.config
        g.db = mysql.connector.connect(
            host=config['DB_HOST'], port=config['DB_PORT'],
            database=config['DB_NAME'], user=config['DB_USER'], password=config['DB_PASSWORD'],
            charset='utf8mb4', connection_timeout=10,
        )
    return g.db


def select(sql, params=()):
    start = perf_counter()
    cursor = connection().cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        cursor.close()
        g.query_count = g.get('query_count', 0) + 1
        g.query_seconds = g.get('query_seconds', 0.0) + perf_counter() - start


def close_connection(error=None):
    conn = g.pop('db', None)
    if conn is not None:
        conn.close()
