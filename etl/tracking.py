"""Arm and disarm Change Tracking and Change Data Capture on the source systems.

Two properties this module has to keep, both learned the hard way on the JDE
build and both asserted by verify.py:

1. A failed switch must not leave a database with no mechanism at all. Arm the
   incoming mechanism before tearing the outgoing one down.

2. Disabling tracking silently invalidates any stored watermark -- changes made
   while a table was untracked are gone, and SQL Server cannot tell you that,
   because CHANGE_TRACKING_MIN_VALID_VERSION only advances when the database
   version advances. So whoever disables tracking must also drop the watermark,
   forcing the next incremental run to re-baseline.
"""
from __future__ import annotations

from core import registry, sqlutil

CT_RETENTION_DAYS = 3


# ---------------------------------------------------------------------------
# Change Tracking
# ---------------------------------------------------------------------------
def ct_database_on(cur) -> bool:
    return bool(sqlutil.scalar(
        cur, "SELECT COUNT(*) FROM sys.change_tracking_databases WHERE database_id = DB_ID()"))


def enable_ct_database(cur, retention_days: int = CT_RETENTION_DAYS) -> str:
    if ct_database_on(cur):
        return "change tracking already on at database level"
    cur.execute(
        "DECLARE @sql nvarchar(max) = N'ALTER DATABASE ' + QUOTENAME(DB_NAME()) + "
        "N' SET CHANGE_TRACKING = ON (CHANGE_RETENTION = " + str(int(retention_days)) +
        " DAYS, AUTO_CLEANUP = ON)'; EXEC sp_executesql @sql;")
    return f"change tracking enabled on {sqlutil.scalar(cur, 'SELECT DB_NAME()')}"


def disable_ct_database(cur) -> str:
    if not ct_database_on(cur):
        return "change tracking already off at database level"
    cur.execute("DECLARE @sql nvarchar(max) = N'ALTER DATABASE ' + QUOTENAME(DB_NAME()) + "
                "N' SET CHANGE_TRACKING = OFF'; EXEC sp_executesql @sql;")
    return "change tracking disabled at database level"


def table_ct_on(cur, spec: registry.TableSpec) -> bool:
    return bool(sqlutil.scalar(
        cur, "SELECT COUNT(*) FROM sys.change_tracking_tables WHERE object_id = OBJECT_ID(?)",
        spec.qualified))


def enable_table_ct(cur, spec: registry.TableSpec) -> str:
    if table_ct_on(cur, spec):
        return f"CT already on {spec.qualified}"
    cur.execute(f"ALTER TABLE {sqlutil.qualify(spec.schema, spec.name)} "
                f"ENABLE CHANGE_TRACKING WITH (TRACK_COLUMNS_UPDATED = OFF)")
    return f"CT enabled on {spec.qualified}"


def disable_table_ct(cur, spec: registry.TableSpec) -> str:
    if not table_ct_on(cur, spec):
        return f"CT already off {spec.qualified}"
    cur.execute(f"ALTER TABLE {sqlutil.qualify(spec.schema, spec.name)} "
                f"DISABLE CHANGE_TRACKING")
    return f"CT disabled on {spec.qualified} (watermark invalidated)"


def ct_current_version(cur) -> int:
    return int(sqlutil.scalar(cur, "SELECT CHANGE_TRACKING_CURRENT_VERSION()") or 0)


def ct_min_valid_version(cur, spec: registry.TableSpec):
    return sqlutil.scalar(
        cur, "SELECT CHANGE_TRACKING_MIN_VALID_VERSION(OBJECT_ID(?))", spec.qualified)


# ---------------------------------------------------------------------------
# Change Data Capture
# ---------------------------------------------------------------------------
def cdc_database_on(cur) -> bool:
    return bool(sqlutil.scalar(
        cur, "SELECT is_cdc_enabled FROM sys.databases WHERE database_id = DB_ID()"))


def enable_cdc_database(cur) -> str:
    """Needs sysadmin on a full SQL Server instance. Azure SQL lets db_owner do it."""
    if cdc_database_on(cur):
        return "CDC already on at database level"
    cur.execute("EXEC sys.sp_cdc_enable_db")
    return f"CDC enabled on {sqlutil.scalar(cur, 'SELECT DB_NAME()')}"


def table_cdc_on(cur, spec: registry.TableSpec) -> bool:
    if not cdc_database_on(cur):
        return False
    return bool(sqlutil.scalar(
        cur, "SELECT COUNT(*) FROM cdc.change_tables WHERE source_object_id = OBJECT_ID(?)",
        spec.qualified))


def enable_table_cdc(cur, spec: registry.TableSpec) -> str:
    if table_cdc_on(cur, spec):
        return f"CDC already on {spec.qualified}"
    cur.execute(
        "EXEC sys.sp_cdc_enable_table @source_schema = ?, @source_name = ?, "
        "@role_name = NULL, @capture_instance = ?, @supports_net_changes = 1",
        spec.schema, spec.name, spec.capture_instance)
    return f"CDC enabled on {spec.qualified}"


def disable_table_cdc(cur, spec: registry.TableSpec) -> str:
    if not table_cdc_on(cur, spec):
        return f"CDC already off {spec.qualified}"
    cur.execute("EXEC sys.sp_cdc_disable_table @source_schema = ?, @source_name = ?, "
                "@capture_instance = ?", spec.schema, spec.name, spec.capture_instance)
    return f"CDC disabled on {spec.qualified} (watermark invalidated)"


def cdc_max_lsn(cur):
    return sqlutil.scalar(cur, "SELECT sys.fn_cdc_get_max_lsn()")


def cdc_min_lsn(cur, spec: registry.TableSpec):
    return sqlutil.scalar(cur, "SELECT sys.fn_cdc_get_min_lsn(?)", spec.capture_instance)


def cdc_capture_healthy(cur) -> tuple[bool, str]:
    """The capture job, not the enable flag, is what populates the change tables.
    A max LSN of NULL means nothing has been captured yet."""
    if not cdc_database_on(cur):
        return False, "CDC is not enabled on this database"
    lsn = cdc_max_lsn(cur)
    if lsn is None:
        return False, ("CDC is enabled but no LSN has been captured yet -- the "
                       "cdc capture job may not be running (SQL Server Agent)")
    last = sqlutil.scalar(cur, "SELECT MAX(tran_end_time) FROM cdc.lsn_time_mapping")
    return True, f"capture healthy, last LSN at {last}"


# ---------------------------------------------------------------------------
# Whole-system operations
# ---------------------------------------------------------------------------
def arm(cur, system_key: str, strategy: str) -> list[str]:
    """Turn on whatever the strategy needs. FULL and INCREMENTAL need nothing."""
    spec = registry.system(system_key)
    notes: list[str] = []
    if strategy == "CT":
        notes.append(enable_ct_database(cur))
        for t in spec.tables:
            notes.append(enable_table_ct(cur, t))
    elif strategy == "CDC":
        notes.append(enable_cdc_database(cur))
        for t in spec.tables:
            notes.append(enable_table_cdc(cur, t))
    return notes


class NotArmed(RuntimeError):
    """Arming reported success but the server does not actually have it on."""


def verify_armed(cur, system_key: str, strategy: str) -> list[str]:
    """Read the mechanism back off the server after arming it.

    Enabling tracking and believing it worked is how a system ends up armed in
    the app and untracked in the database -- the state a rebuild leaves behind.
    Every arm is checked against sys.change_tracking_tables / cdc.change_tables
    before the mode is recorded, so a switch either delivers what it claims or
    fails loudly with the tables named.
    """
    spec = registry.system(system_key)
    if strategy == "CT":
        if not ct_database_on(cur):
            raise NotArmed(f"{system_key}: CHANGE_TRACKING is not on at database level")
        missing = [t.qualified for t in spec.tables if not table_ct_on(cur, t)]
        if missing:
            raise NotArmed(f"{system_key}: change tracking did not take on "
                           + ", ".join(missing))
        return [f"verified: CT active on all {len(spec.tables)} table(s), "
                f"database at version {ct_current_version(cur)}"]

    if strategy == "CDC":
        if not cdc_database_on(cur):
            raise NotArmed(f"{system_key}: CDC is not on at database level")
        missing = [t.qualified for t in spec.tables if not table_cdc_on(cur, t)]
        if missing:
            raise NotArmed(f"{system_key}: CDC capture instance missing for "
                           + ", ".join(missing))
        notes = [f"verified: CDC capture instances exist for all "
                 f"{len(spec.tables)} table(s)"]
        # The capture job, not the enable flag, is what fills the change tables.
        # A cold Agent is a warning, not a failed arm -- it may still spin up.
        healthy, msg = cdc_capture_healthy(cur)
        notes.append(msg if healthy else f"warning: {msg}")
        return notes

    return []


def disarm(cur, system_key: str, strategy: str) -> list[str]:
    spec = registry.system(system_key)
    notes: list[str] = []
    if strategy == "CT":
        for t in spec.tables:
            notes.append(disable_table_ct(cur, t))
    elif strategy == "CDC":
        for t in spec.tables:
            notes.append(disable_table_cdc(cur, t))
    return notes


def disable_all(cur, system_key: str) -> list[str]:
    """Take every mechanism off, so the tables can be dropped."""
    spec = registry.system(system_key)
    notes: list[str] = []
    for t in spec.tables:
        try:
            notes.append(disable_table_cdc(cur, t))
        except Exception as exc:
            notes.append(f"CDC disable skipped on {t.qualified}: {exc}")
        try:
            notes.append(disable_table_ct(cur, t))
        except Exception as exc:
            notes.append(f"CT disable skipped on {t.qualified}: {exc}")
    return [n for n in notes if "already off" not in n]


def state(cur, system_key: str) -> dict:
    """Everything the dashboard shows about one system's change mechanisms."""
    spec = registry.system(system_key)
    db_ct = ct_database_on(cur)
    db_cdc = cdc_database_on(cur)
    tables = []
    for t in spec.tables:
        row = {
            "table": t.qualified,
            "label": t.label,
            "rows": sqlutil.row_count(cur, t.schema, t.name),
            "ct": table_ct_on(cur, t) if db_ct else False,
            "cdc": table_cdc_on(cur, t) if db_cdc else False,
        }
        row["ct_min_version"] = ct_min_valid_version(cur, t) if row["ct"] else None
        tables.append(row)
    out = {
        "system": system_key,
        "db_change_tracking": db_ct,
        "db_cdc": db_cdc,
        "ct_current_version": ct_current_version(cur) if db_ct else None,
        "tables": tables,
    }
    if db_cdc:
        healthy, msg = cdc_capture_healthy(cur)
        out["cdc_healthy"], out["cdc_message"] = healthy, msg
    return out
