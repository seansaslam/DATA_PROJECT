"""Open live connections to the application databases from stored profiles."""
from __future__ import annotations

import contextlib
import time

from . import sqlutil
from .models import profile


@contextlib.contextmanager
def open(system_key: str, autocommit: bool = False, timeout: int = 15):
    """Connect to one system's database using its saved profile."""
    prof = profile(system_key)
    with sqlutil.connect(prof.connection_string(timeout=timeout), autocommit=autocommit) as conn:
        yield conn


def test(system_key: str) -> dict:
    """Probe a connection and report everything the Connections page shows."""
    prof = profile(system_key)
    started = time.perf_counter()
    result = {
        "system": system_key,
        "label": prof.label,
        "target": prof.describe(),
        "db_type": prof.dialect.label,
        "ok": False,
    }
    try:
        with sqlutil.connect(prof.connection_string(timeout=8), autocommit=True) as conn:
            cur = conn.cursor()
            cur.execute("SELECT DB_NAME(), SUSER_NAME(), "
                        "CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(64)), "
                        "CAST(SERVERPROPERTY('Edition') AS nvarchar(128))")
            db, login, version, edition = cur.fetchone()
            result.update(
                ok=True, database=db, login=login, version=version, edition=edition,
                is_db_owner=bool(sqlutil.scalar(cur, "SELECT IS_ROLEMEMBER('db_owner')")),
                is_sysadmin=bool(sqlutil.scalar(cur, "SELECT IS_SRVROLEMEMBER('sysadmin')")),
                table_count=int(sqlutil.scalar(cur, "SELECT COUNT(*) FROM sys.tables") or 0),
            )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return result
