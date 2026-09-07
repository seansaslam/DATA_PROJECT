"""Thin pyodbc helpers shared by every layer that touches a system database."""
from __future__ import annotations

import contextlib
import datetime as dt
import re
import struct

import pyodbc

# Identifiers we build SQL from come from registry.py, never from user input,
# but everything still goes through here so that assumption is enforced in one
# place rather than trusted in twenty.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")

# SQL_SS_TIMESTAMPOFFSET. No column in these sources is a datetimeoffset, but
# AT TIME ZONE returns one, and pyodbc has no built-in decoder -- so any timezone
# conversion comes back as "ODBC SQL type -155 is not yet supported" rather than
# a value. The converter is registered on every connection so the transformations
# scratchpad can run a UTC-to-local query as written.
_SQL_SS_TIMESTAMPOFFSET = -155


def _decode_datetimeoffset(raw: bytes) -> dt.datetime:
    """20 bytes: y, mo, d, h, mi, s as int16, nanoseconds as uint32, then the
    offset as two int16."""
    year, month, day, hour, minute, second, nanos, tz_h, tz_m = struct.unpack(
        "<6hI2h", raw)
    return dt.datetime(year, month, day, hour, minute, second, nanos // 1000,
                       dt.timezone(dt.timedelta(hours=tz_h, minutes=tz_m)))


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
    conn.add_output_converter(_SQL_SS_TIMESTAMPOFFSET, _decode_datetimeoffset)
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
