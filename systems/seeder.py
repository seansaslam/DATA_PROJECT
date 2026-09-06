"""Create the schemas and load CCT Oil & Cattle into the three source databases.

Volumes, for the 2026 year-to-date build:

    wm.cct_well_master      2,003 rows   (603 operated + 1,400 non-operated)
    pc.pc_completion          603 rows   (operated only -- ProCount is the
                                          production accounting system, and CCT
                                          does not do accounting for wells it
                                          does not operate)
    pc.well_daily_prod     ~135,000 rows (operated, first production -> as-of)
    pc.pc_daily_downtime     ~7,500 rows (only days that actually lost time)
    ac.AC_PROPERTY            603 rows
    ac.AC_DAILY            ~220,000 rows (operated, full calendar 2026)
"""
from __future__ import annotations

import datetime as dt
import random

from core import connections, registry, sqlutil

from . import generator
from .generator import Well

BATCH = 5000


def _insert_sql(spec: registry.TableSpec) -> str:
    return (f"INSERT INTO {sqlutil.qualify(spec.schema, spec.name)} "
            f"({sqlutil.col_list(spec.columns)}) "
            f"VALUES ({sqlutil.placeholders(len(spec.columns))})")


# ---------------------------------------------------------------------------
# Schema management
# ---------------------------------------------------------------------------
def create_schema(system_key: str, log=print) -> None:
    spec = registry.system(system_key)
    script = spec.ddl_path.read_text(encoding="utf-8")
    with connections.open(system_key, autocommit=True) as conn:
        sqlutil.exec_batches(conn.cursor(), script)
    log(f"{spec.label}: schema [{spec.schema}] created / verified")


def drop_schema(system_key: str, log=print) -> None:
    """Drop every table this system owns. Tracking has to come off first --
    SQL Server refuses to drop a change-tracked or CDC-captured table."""
    from etl import tracking

    spec = registry.system(system_key)
    with connections.open(system_key, autocommit=True) as conn:
        cur = conn.cursor()
        for note in tracking.disable_all(cur, system_key):
            log(f"  {note}")
        tables = [t.name for t in reversed(spec.tables)]
        for name in tables:
            cur.execute(f"IF OBJECT_ID(?) IS NOT NULL "
                        f"DROP TABLE {sqlutil.qualify(spec.schema, name)}",
                        f"{spec.schema}.{name}")
        log(f"{spec.label}: {len(tables)} tables dropped")


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------
def seed_well_master(wells: list[Well], log=print) -> int:
    spec = registry.WM_MASTER
    rows = [
        (w.api_uwi, w.well_name, w.well_number, w.operator_name, 1 if w.operated else 0,
         w.area, w.pad_name, w.central_facility, w.field_name, w.county, w.state_code,
         w.basin, w.reservoir, w.well_status, w.well_type,
         w.surface_lat, w.surface_lon, w.bh_lat, w.bh_lon,
         w.spud_date, w.completion_date, w.first_prod_date,
         w.lateral_ft, w.total_depth_ft, w.wi, w.nri)
        for w in wells
    ]
    with connections.open("wellmaster") as conn:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM {sqlutil.qualify(spec.schema, spec.name)}")
        sqlutil.executemany(cur, _insert_sql(spec), rows, BATCH)
    log(f"  wm.cct_well_master        {len(rows):>9,} rows")
    return len(rows)


def seed_procount(wells: list[Well], asof: dt.date, log=print, progress=None) -> dict:
    operated = [w for w in wells if w.operated]
    rng = random.Random(generator.SEED + 7)
    counts = {"pc_completion": 0, "well_daily_prod": 0, "pc_daily_downtime": 0}

    comp_rows = [
        (w.merrick_id, w.api_uwi, f"{w.well_name} {w.well_number}", w.prod_route,
         w.battery_id, w.battery_name, w.area,
         0 if (w.shut_in_from and w.shut_in_from <= asof) else 1,
         w.first_prod_date,
         rng.choices(["TEST", "METER", "THEORETICAL"], weights=[62, 30, 8])[0])
        for w in operated
    ]

    with connections.open("procount") as conn:
        cur = conn.cursor()
        for spec in (registry.PC_COMPLETION, registry.PC_DAILY, registry.PC_DOWNTIME):
            cur.execute(f"DELETE FROM {sqlutil.qualify(spec.schema, spec.name)}")

        sqlutil.executemany(cur, _insert_sql(registry.PC_COMPLETION), comp_rows, BATCH)
        counts["pc_completion"] = len(comp_rows)

        daily_sql = _insert_sql(registry.PC_DAILY)
        down_sql = _insert_sql(registry.PC_DOWNTIME)
        daily_buf: list[tuple] = []
        down_buf: list[tuple] = []

        for n, w in enumerate(operated, 1):
            start = max(w.first_prod_date, generator.PROD_START)
            seq_by_day: dict[dt.date, int] = {}
            for day in generator.date_range(start, asof):
                a = generator.actual_day(w, day, rng)
                daily_buf.append((
                    w.merrick_id, day, w.api_uwi,
                    a["oil"], a["gas"], a["water"], a["ngl"],
                    round(a["oil"] * w.nri, 2), round(a["gas"] * w.nri, 2),
                    round(a["ngl"] * w.nri, 2),
                    a["producing_hours"], a["downtime_hours"],
                    a["tubing"], a["casing"], a["choke"],
                    a["status"], generator.allocation_status(day, asof),
                ))
                if a["downtime"]:
                    code, desc, hours, def_oil, def_gas = a["downtime"]
                    seq = seq_by_day.get(day, 0) + 1
                    seq_by_day[day] = seq
                    down_buf.append((w.merrick_id, day, seq, w.api_uwi, hours,
                                     code, desc, def_oil, def_gas))

            if len(daily_buf) >= BATCH * 6:
                counts["well_daily_prod"] += sqlutil.executemany(cur, daily_sql, daily_buf, BATCH)
                daily_buf = []
            if len(down_buf) >= BATCH * 2:
                counts["pc_daily_downtime"] += sqlutil.executemany(cur, down_sql, down_buf, BATCH)
                down_buf = []
            if progress and n % 60 == 0:
                progress(f"  ProCount: {n}/{len(operated)} wells "
                         f"({counts['well_daily_prod'] + len(daily_buf):,} daily rows)")

        counts["well_daily_prod"] += sqlutil.executemany(cur, daily_sql, daily_buf, BATCH)
        counts["pc_daily_downtime"] += sqlutil.executemany(cur, down_sql, down_buf, BATCH)

    log(f"  pc.pc_completion          {counts['pc_completion']:>9,} rows")
    log(f"  pc.well_daily_prod        {counts['well_daily_prod']:>9,} rows")
    log(f"  pc.pc_daily_downtime      {counts['pc_daily_downtime']:>9,} rows")
    return counts


def seed_aries(wells: list[Well], log=print, progress=None) -> dict:
    operated = [w for w in wells if w.operated]
    counts = {"AC_PROPERTY": 0, "AC_DAILY": 0}
    effective = generator.PROD_START

    prop_rows = [
        (w.propnum, w.api_uwi, f"{w.well_name} {w.well_number}",
         w.well_name, w.operator_name, w.area, w.pad_name, w.field_name, w.county,
         w.state_code, w.reservoir, w.well_type, w.well_status, w.first_prod_date,
         w.surface_lat, w.surface_lon, w.wi, w.nri,
         "2026 BUDGET", w.reserve_cat, w.type_curve, effective,
         w.qi_oil, w.di_nominal, w.b_factor)
        for w in operated
    ]

    with connections.open("aries") as conn:
        cur = conn.cursor()
        for spec in (registry.AC_DAILY, registry.AC_PROPERTY):
            cur.execute(f"DELETE FROM {sqlutil.qualify(spec.schema, spec.name)}")

        sqlutil.executemany(cur, _insert_sql(registry.AC_PROPERTY), prop_rows, BATCH)
        counts["AC_PROPERTY"] = len(prop_rows)

        daily_sql = _insert_sql(registry.AC_DAILY)
        buf: list[tuple] = []
        for n, w in enumerate(operated, 1):
            for day in generator.date_range(generator.PROD_START, generator.FCST_END):
                oil, gas, water, ngl = generator.forecast_day(w, day)
                status = w.well_status
                if w.shut_in_from and day >= w.shut_in_from:
                    status = generator.WELL_STATUS_SHUT_IN
                elif day < w.first_prod_date:
                    status = "NOT ONLINE"
                buf.append((
                    w.propnum, day, w.api_uwi, f"{w.well_name} {w.well_number}",
                    w.area, w.pad_name, status, w.well_type,
                    oil, gas, water, ngl,
                    round(oil * w.nri, 2), round(gas * w.nri, 2), round(ngl * w.nri, 2),
                    "2026 BUDGET", w.reserve_cat, "BASE",
                ))
            if len(buf) >= BATCH * 6:
                counts["AC_DAILY"] += sqlutil.executemany(cur, daily_sql, buf, BATCH)
                buf = []
            if progress and n % 60 == 0:
                progress(f"  ARIES: {n}/{len(operated)} wells "
                         f"({counts['AC_DAILY'] + len(buf):,} forecast rows)")

        counts["AC_DAILY"] += sqlutil.executemany(cur, daily_sql, buf, BATCH)

    log(f"  ac.AC_PROPERTY            {counts['AC_PROPERTY']:>9,} rows")
    log(f"  ac.AC_DAILY               {counts['AC_DAILY']:>9,} rows")
    return counts


def seed_all(asof: dt.date | None = None, log=print, progress=None) -> dict:
    """Create every schema and load the full company. Idempotent: safe to re-run."""
    asof = asof or generator.today()
    progress = progress or log

    for key in registry.SOURCE_SYSTEMS:
        create_schema(key, log)

    log(f"Generating {registry.OPERATED_WELLS + registry.NON_OPERATED_WELLS:,} wells ...")
    wells = generator.build_wells()

    result = {"wells": seed_well_master(wells, log)}
    result.update(seed_procount(wells, asof, log, progress))
    result.update(seed_aries(wells, log, progress))
    result["asof"] = asof.isoformat()

    total = sum(v for v in result.values() if isinstance(v, int))
    log(f"Seed complete: {total:,} rows across three source databases.")
    return result
