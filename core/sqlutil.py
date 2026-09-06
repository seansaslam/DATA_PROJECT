"""Thin pyodbc helpers shared by every layer that touches a system database."""
from __future__ import annotations

import contextlib
import re

import pyodbc

# Identifiers we build SQL from come from registry.py, never from user input,
# but everything still goes through here so that assumption is enforced in one
# place rather than trusted in twenty.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"refusing to build SQL with identifier {name!r}")
    return f"[{name}]"


def qualify(schema: str, table: str) -> str:
    return f"{ident(schema)}.{ident(table)}"


def col_list(columns) -> str:
    return ", ".join(ident(c) for c in columns)


def placeholders(n: int) -> str:
    return ", ".join(["?"] * n)


@contextlib.contextmanager
def connect(conn_str: str, autocommit: bool = False):
    """Yield a connection, committing on clean exit and rolling back on error."""
    conn = pyodbc.connect(conn_str, autocommit=autocommit)
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            with contextlib.suppress(Exception):
                conn.rollback()
        raise
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def split_batches(script: str) -> list[str]:
    """Split a T-SQL script on GO separators."""
    batches, current = [], []
    for line in script.splitlines():
        if line.strip().upper() == "GO":
            chunk = "\n".join(current).strip()
            if chunk:
                batches.append(chunk)
            current = []
        else:
            current.append(line)
    chunk = "\n".join(current).strip()
    if chunk:
        batches.append(chunk)
    return batches


def exec_batches(cursor, script: str) -> None:
    for batch in split_batches(script):
        cursor.execute(batch)
        while cursor.nextset():          # drain, so the next execute is clean
            pass


def scalar(cursor, sql: str, *params):
    cursor.execute(sql, *params)
    row = cursor.fetchone()
    return None if row is None else row[0]


def rows_as_dicts(cursor) -> list[dict]:
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, r)) for r in cursor.fetchall()]


def table_exists(cursor, schema: str, table: str) -> bool:
    return scalar(cursor,
                  "SELECT OBJECT_ID(?)", f"{schema}.{table}") is not None


def row_count(cursor, schema: str, table: str, where: str = "") -> int:
    if not table_exists(cursor, schema, table):
        return -1
    sql = f"SELECT COUNT_BIG(*) FROM {qualify(schema, table)}"
    if where:
        sql += f" WHERE {where}"
    return int(scalar(cursor, sql) or 0)


def executemany(cursor, sql: str, rows: list[tuple], batch: int = 2000) -> int:
    """Insert in chunks with fast_executemany, which is the difference between
    a 20-second seed and a 20-minute one."""
    if not rows:
        return 0
    prior = cursor.fast_executemany
    cursor.fast_executemany = True
    try:
        for i in range(0, len(rows), batch):
            cursor.executemany(sql, rows[i:i + batch])
    finally:
        cursor.fast_executemany = prior
    return len(rows)
