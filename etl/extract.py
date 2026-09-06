"""The four extraction strategies, side by side on the same tables.

Every reader returns rows in exactly TableSpec.target_columns order, so the
loader is identical for all four. What differs is how much they read, whether
they can see deletes at all, and what they have to remember between runs.

    FULL         reads everything, needs no mechanism, cannot be wrong
    INCREMENTAL  reads WHERE UPDATED_TS > watermark -- cheap, but blind to deletes
    CT           reads CHANGETABLE, learns which rows changed, joins back for values
    CDC          reads the capture table, which already carries the values
"""
from __future__ import annotations

import re

from core import registry, sqlutil

_CAPTURE_INSTANCE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _select_list(spec: registry.TableSpec, prefix: str = "") -> str:
    p = f"{prefix}." if prefix else ""
    return ", ".join(f"{p}{sqlutil.ident(c)}" for c in spec.select_columns)


# ---------------------------------------------------------------------------
# FULL
# ---------------------------------------------------------------------------
def full(cur, spec: registry.TableSpec) -> list[tuple]:
    cur.execute(f"SELECT {_select_list(spec)} "
                f"FROM {sqlutil.qualify(spec.schema, spec.name)}")
    return [tuple(r) for r in cur.fetchall()]


def max_audit(cur, spec: registry.TableSpec):
    return sqlutil.scalar(
        cur, f"SELECT MAX({sqlutil.ident(spec.audit_col)}) "
             f"FROM {sqlutil.qualify(spec.schema, spec.name)}")


# ---------------------------------------------------------------------------
# INCREMENTAL -- audit column watermark
# ---------------------------------------------------------------------------
def incremental(cur, spec: registry.TableSpec, high_water):
    """Rows whose audit column moved past the watermark.

    Deletes are invisible to this strategy by construction: a row that is gone
    has no UPDATED_TS to compare. That is the point of the exercise -- it is the
    cheapest strategy and the one that quietly drifts.
    """
    audit = sqlutil.ident(spec.audit_col)
    src = sqlutil.qualify(spec.schema, spec.name)
    if high_water is None:
        cur.execute(f"SELECT {_select_list(spec)} FROM {src}")
        baseline = True
    else:
        cur.execute(f"SELECT {_select_list(spec)} FROM {src} WHERE {audit} > ?", high_water)
        baseline = False
    rows = [tuple(r) for r in cur.fetchall()]
    new_hw = max((r[-1] for r in rows), default=high_water)
    return rows, new_hw, baseline


# ---------------------------------------------------------------------------
# CT -- Change Tracking
# ---------------------------------------------------------------------------
def ct(cur, spec: registry.TableSpec, last_version):
    """CHANGETABLE tells you which rows changed, not what they used to hold, so
    the current values come from a join back to the base table."""
    from . import tracking

    current = tracking.ct_current_version(cur)
    min_valid = tracking.ct_min_valid_version(cur, spec)

    # MIN_VALID_VERSION is NULL when the table is not tracked at all, which is
    # what a rebuild leaves behind: dropping a table takes its change tracking
    # with it, while CHANGE_TRACKING_CURRENT_VERSION keeps advancing at the
    # database level so a stored watermark still looks plausible. NULL is the
    # strongest possible signal that the watermark cannot be honoured -- reading
    # it as "no opinion" is what walks straight into CHANGETABLE and error 22105.
    untracked = min_valid is None
    baseline = last_version is None or untracked or last_version < min_valid
    if baseline:
        if last_version is None:
            note = "no watermark -- baselining"
        elif untracked:
            note = (f"{spec.qualified} is not change-tracked, so watermark "
                    f"{last_version} cannot be honoured -- re-baselining. Arm this "
                    "system for CT again to start collecting changes.")
        else:
            note = (f"watermark {last_version} is below the minimum valid version "
                    f"{min_valid}; retained history no longer covers it, re-baselining")
        return full(cur, spec), [], current, True, note

    src = sqlutil.qualify(spec.schema, spec.name)
    join = " AND ".join(f"s.{sqlutil.ident(c)} = ct.{sqlutil.ident(c)}" for c in spec.pk)
    cols = ", ".join(
        (f"ct.{sqlutil.ident(c)}" if c in spec.pk else f"s.{sqlutil.ident(c)}")
        for c in spec.select_columns
    )
    cur.execute(
        f"SELECT ct.SYS_CHANGE_OPERATION, {cols} "
        f"FROM CHANGETABLE(CHANGES {src}, ?) AS ct "
        f"LEFT JOIN {src} AS s ON {join}", last_version)

    rows, deletes = [], []
    npk = len(spec.pk)
    pk_pos = [spec.select_columns.index(c) for c in spec.pk]
    for r in cur.fetchall():
        op, values = r[0], tuple(r[1:])
        if op == "D":
            deletes.append(tuple(values[i] for i in pk_pos))
        else:
            rows.append(values)
    note = f"read {len(rows)} upserts and {len(deletes)} deletes between version {last_version} and {current}"
    return rows, deletes, current, False, note


# ---------------------------------------------------------------------------
# CDC -- Change Data Capture
# ---------------------------------------------------------------------------
def cdc(cur, spec: registry.TableSpec, from_lsn):
    """The capture tables carry the column values themselves, so unlike CT there
    is no join back to the source -- which is also why CDC can show you a row
    that no longer exists."""
    from . import tracking

    ci = spec.capture_instance
    if not _CAPTURE_INSTANCE.match(ci):
        raise ValueError(f"bad capture instance {ci!r}")

    to_lsn = tracking.cdc_max_lsn(cur)
    if to_lsn is None:
        return [], [], None, False, ("CDC has captured no LSN yet -- is the capture "
                                     "job running? Nothing to read.")
    min_lsn = tracking.cdc_min_lsn(cur, spec)

    # As with CT: fn_cdc_get_min_lsn returns NULL when this capture instance does
    # not exist -- the table was dropped and recreated, or CDC was never armed on
    # it -- and the watermark is then unanswerable rather than merely stale.
    uncaptured = min_lsn is None
    baseline = from_lsn is None or uncaptured or from_lsn < min_lsn
    if baseline:
        if from_lsn is None:
            note = "no watermark -- baselining"
        elif uncaptured:
            note = (f"{spec.qualified} has no CDC capture instance, so the stored "
                    "LSN cannot be honoured -- re-baselining. Arm this system for "
                    "CDC again to start collecting changes.")
        else:
            note = "watermark is older than the retained CDC history, re-baselining"
        return full(cur, spec), [], to_lsn, True, note

    next_lsn = sqlutil.scalar(cur, "SELECT sys.fn_cdc_increment_lsn(?)", from_lsn)
    if next_lsn > to_lsn:
        return [], [], from_lsn, False, "no new LSN since the last run"

    cols = _select_list(spec)
    cur.execute(
        f"SELECT __$operation, {cols} "
        f"FROM cdc.fn_cdc_get_net_changes_{ci}(?, ?, 'all')", next_lsn, to_lsn)

    rows, deletes = [], []
    pk_pos = [spec.select_columns.index(c) for c in spec.pk]
    for r in cur.fetchall():
        op, values = r[0], tuple(r[1:])
        if op == 1:                       # delete
            deletes.append(tuple(values[i] for i in pk_pos))
        else:                             # 2 = insert, 5 = update (net changes)
            rows.append(values)
    note = f"read {len(rows)} upserts and {len(deletes)} deletes from the capture table"
    return rows, deletes, to_lsn, False, note
