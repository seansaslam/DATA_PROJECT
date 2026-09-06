"""Ingestion readiness: arm a source, then see exactly what a load would pull.

There is no warehouse here on purpose. ADF or Fivetran will move these tables to
Snowflake; this app's job is to make the *source side* real and legible -- arm
the change mechanism the ingestion tool needs, then answer the question a data
engineer actually has to answer before wiring anything up:

    if my pipeline ran right now, how many rows would it move, and would it
    catch the deletes?

A "load" here extracts and counts. It does not write the rows anywhere. What it
records is the number, in core.models.LoadRun, so the same churn read four
different ways can be compared side by side.
"""
from __future__ import annotations

import time

from core import connections, registry
from core.models import ActivityLog, LoadRun, SystemMode

from . import extract, tracking, watermark

SAMPLE_ROWS = 8


# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------
def get_mode(system_key: str) -> str:
    row = SystemMode.objects.filter(system_key=system_key).first()
    return row.strategy if row else "FULL"


def all_modes() -> dict[str, str]:
    return {k: get_mode(k) for k in registry.SOURCE_SYSTEMS}


def switch_mode(system_key: str, strategy: str, rebuild: bool = False, log=None) -> dict:
    """Arm the incoming mechanism, reset the watermarks, then disarm the outgoing one.

    Order matters. Disarming first means a switch that fails on a permission
    check -- CDC needs sysadmin on-prem -- leaves the database with no mechanism
    at all, and the next incremental load reports zero changes while the source
    quietly drifts.

    `rebuild` drops and recreates the source tables, giving a clean slate. It is
    off by default because it destroys the seeded production history; the UI asks
    before setting it.
    """
    log = log or (lambda _m: None)
    strategy = (strategy or "").upper()
    if strategy not in registry.STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; expected one of "
                         f"{', '.join(registry.STRATEGIES)}")

    previous = get_mode(system_key)
    notes: list[str] = []

    if rebuild:
        from systems import seeder
        log(f"rebuilding {system_key} tables from DDL ...")
        seeder.drop_schema(system_key, log)
        seeder.create_schema(system_key, log)
        notes.append("source tables dropped and recreated -- they are empty until you reseed")

    try:
        with connections.open(system_key, autocommit=True) as conn:
            cur = conn.cursor()
            notes += tracking.arm(cur, system_key, strategy)       # arm first
            notes += tracking.verify_armed(cur, system_key, strategy)   # prove it
            cleared = watermark.clear(system_key)
            notes.append(f"cleared {cleared} watermark row(s); the next load will baseline")
            if previous != strategy:
                notes += tracking.disarm(cur, system_key, previous)     # disarm last
    except Exception:
        # Arming is verified before anything is torn down, so a failure here
        # leaves the outgoing mechanism live and the recorded mode unchanged.
        for n in notes:
            log(f"  {n}")
        log(f"  {system_key} stays on {previous}; nothing was disarmed")
        raise

    SystemMode.objects.update_or_create(system_key=system_key,
                                        defaults={"strategy": strategy})
    ActivityLog.objects.create(system_key=system_key, action="MODE",
                               detail=f"{previous} -> {strategy}"
                                      + (" (tables rebuilt)" if rebuild else ""))
    for n in notes:
        log(f"  {n}")
    return {"system": system_key, "previous": previous, "strategy": strategy,
            "rebuild": rebuild, "notes": notes}


def rearm(system_key: str, log=None) -> list[str]:
    """Put the source back into the state the recorded mode already claims.

    Dropping a table takes its change tracking and its CDC capture instance with
    it, but the mode is app-side state in SQLite and survives the rebuild. So
    anything that recreates the tables has to re-arm them, or the app reports CT
    while the database tracks nothing -- and the first read after that baselines
    happily, stores a database-level version no table can answer for, and the
    read after *that* fails on CHANGETABLE.
    """
    log = log or (lambda _m: None)
    mode = get_mode(system_key)
    if mode not in ("CT", "CDC"):
        return []
    notes: list[str] = []
    try:
        with connections.open(system_key, autocommit=True) as conn:
            cur = conn.cursor()
            notes += tracking.arm(cur, system_key, mode)
            notes += tracking.verify_armed(cur, system_key, mode)
    except Exception as exc:
        notes.append(f"could not re-arm {system_key} for {mode}: "
                     f"{type(exc).__name__}: {exc}")
        notes.append(f"{system_key} still reads as {mode} but is not tracked -- "
                     "every read will baseline until it is armed again")
    for n in notes:
        log(f"  {n}")
    return notes


def rearm_all(log=None) -> dict[str, list[str]]:
    """Re-arm every system for whatever mode it is recorded as. Called after a
    rebuild, from the dashboard and from seed_cli alike."""
    return {k: rearm(k, log) for k in registry.SOURCE_SYSTEMS}


# ---------------------------------------------------------------------------
# Reading a change feed
# ---------------------------------------------------------------------------
def read_table(cur, system_key: str, spec: registry.TableSpec, strategy: str,
               commit: bool = True) -> dict:
    """Extract what this strategy would deliver for one table, and record it."""
    started = time.perf_counter()
    wm = watermark.read(system_key, spec, strategy)
    notes: list[str] = []
    deletes: list[tuple] = []
    baseline = False
    new_ct = new_lsn = new_hw = None

    if strategy == "FULL":
        rows = extract.full(cur, spec)
        new_hw = extract.max_audit(cur, spec)
        baseline = True
        notes.append(f"reads every row, every time: {len(rows):,}")

    elif strategy == "INCREMENTAL":
        rows, new_hw, baseline = extract.incremental(cur, spec, wm.get("high_water_ts"))
        notes.append("no watermark yet -- first load reads everything" if baseline else
                     f"{len(rows):,} rows with UPDATED_TS > {wm.get('high_water_ts')}")
        notes.append("hard deletes are invisible to this strategy by construction")

    elif strategy == "CT":
        rows, deletes, new_ct, baseline, note = extract.ct(cur, spec, wm.get("ct_version"))
        notes.append(note)

    elif strategy == "CDC":
        rows, deletes, new_lsn, baseline, note = extract.cdc(cur, spec, wm.get("cdc_lsn"))
        notes.append(note)

    else:
        raise ValueError(f"unknown strategy {strategy!r}")

    if commit:
        watermark.write(system_key, spec, strategy, ct_version=new_ct,
                        cdc_lsn=new_lsn, high_water_ts=new_hw)
    else:
        notes.append("watermark NOT advanced -- run again to read the same rows")

    duration = int((time.perf_counter() - started) * 1000)
    LoadRun.objects.create(
        system_key=system_key, source_table=spec.qualified, strategy=strategy,
        rows_read=len(rows), rows_upserted=len(rows), rows_deleted=len(deletes),
        duration_ms=duration, baseline=baseline, committed=commit,
        note="; ".join(notes)[:600])

    return {
        "system": system_key, "table": spec.qualified, "label": spec.label,
        "strategy": strategy, "baseline": baseline, "committed": commit,
        "read": len(rows), "deleted": len(deletes), "duration_ms": duration,
        "notes": notes,
        "columns": list(spec.select_columns),
        "sample": [_sample(r) for r in rows[:SAMPLE_ROWS]],
        "delete_keys": [_sample(k) for k in deletes[:SAMPLE_ROWS]],
    }


def _sample(row):
    import datetime as dt
    out = []
    for v in row:
        if isinstance(v, dt.datetime):
            out.append(v.isoformat(sep=" ", timespec="milliseconds"))
        elif isinstance(v, dt.date):
            out.append(v.isoformat())
        elif isinstance(v, (bytes, bytearray)):
            out.append(v.hex())
        elif hasattr(v, "quantize"):
            out.append(float(v))
        else:
            out.append(v)
    return out


def run_system(system_key: str, strategy: str | None = None, commit: bool = True,
               log=None) -> dict:
    log = log or (lambda _m: None)
    spec = registry.system(system_key)
    mode = (strategy or get_mode(system_key)).upper()
    results = []
    with connections.open(system_key, autocommit=True) as conn:
        cur = conn.cursor()
        for table in spec.tables:
            log(f"{spec.label} / {table.name}  [{mode}]")
            r = read_table(cur, system_key, table, mode, commit)
            log(f"  would move {r['read']:,} rows, {r['deleted']:,} deletes "
                f"({r['duration_ms']} ms)"
                + ("  -- BASELINE" if r["baseline"] else ""))
            for n in r["notes"]:
                log(f"    {n}")
            results.append(r)
    return {"system": system_key, "strategy": mode, "tables": results,
            "read": sum(r["read"] for r in results),
            "deleted": sum(r["deleted"] for r in results)}


def run_all(commit: bool = True, log=None) -> dict:
    log = log or (lambda _m: None)
    out = {"systems": [run_system(k, commit=commit, log=log)
                       for k in registry.SOURCE_SYSTEMS]}
    out["read"] = sum(s["read"] for s in out["systems"])
    out["deleted"] = sum(s["deleted"] for s in out["systems"])
    log(f"Total across all three systems: {out['read']:,} rows, {out['deleted']:,} deletes.")
    return out


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
def recent_runs(limit: int = 60) -> list[dict]:
    return [
        {"id": r.id, "run_ts": r.run_ts.isoformat(sep=" ", timespec="seconds"),
         "system_key": r.system_key, "source_table": r.source_table,
         "strategy": r.strategy, "rows_read": r.rows_read,
         "rows_deleted": r.rows_deleted, "duration_ms": r.duration_ms,
         "baseline": r.baseline, "committed": r.committed, "note": r.note}
        for r in LoadRun.objects.all()[:limit]
    ]


def recent_activity(limit: int = 40) -> list[dict]:
    return [
        {"id": a.id, "event_ts": a.event_ts.isoformat(sep=" ", timespec="seconds"),
         "system_key": a.system_key, "action": a.action,
         "row_count": a.row_count, "detail": a.detail}
        for a in ActivityLog.objects.all()[:limit]
    ]


def log_activity(system_key, action, detail="", rows=0) -> None:
    ActivityLog.objects.create(system_key=system_key or "", action=action,
                               detail=str(detail)[:600], row_count=rows)
