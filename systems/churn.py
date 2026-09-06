"""Random activity, so the source systems keep moving the way a real one does.

One tick is one operational cycle at CCT Oil & Cattle:

    pumpers report          a new production day lands in ProCount
    accounting revises      estimated volumes firm up to allocated, then final
    engineering re-forecasts a handful of ARIES curves get re-run
    land and ops edit       wells are shut in, returned, renamed, re-interested
    drilling delivers       new wells appear in all three systems at once
    somebody fat-fingers    a bad row is deleted outright

The last one matters most for the training exercise: a hard delete is the only
change a watermarked incremental load cannot see. Run a churn tick, read the
change feed under each strategy, and the row counts tell the story on their own.
"""
from __future__ import annotations

import datetime as dt
import random

from core import connections, registry, sqlutil

from . import generator
from .generator import Well


# ---------------------------------------------------------------------------
# Reading the current state back out of the three systems
# ---------------------------------------------------------------------------
def _load_models() -> dict[str, Well]:
    """Rebuild the rate model for every operated well from what the databases hold.

    Well Master supplies dates, type and interests; ARIES supplies the Arps
    constants; the gas-oil ratio and water cut come back from the API itself
    (generator.model_params). Nothing is cached between ticks, so a well the
    churn engine created five ticks ago behaves exactly like a seeded one.
    """
    with connections.open("wellmaster", autocommit=True) as conn:
        conn.cursor().execute("SELECT 1")
        cur = conn.cursor()
        cur.execute("SELECT API_UWI, WELL_NAME, WELL_NUMBER, AREA, PAD_NAME, "
                    "CENTRAL_FACILITY, COUNTY, WELL_TYPE, WELL_STATUS, FIRST_PROD_DATE, "
                    "NET_REVENUE_INTEREST FROM wm.cct_well_master WHERE OPERATED_FLAG = 1")
        wm = {r[0]: r for r in cur.fetchall()}

    with connections.open("aries", autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute("SELECT API_UWI, PROPNUM, QI_OIL_BOPD, DI_NOMINAL, B_FACTOR, RESERVE_CAT "
                    "FROM ac.AC_PROPERTY")
        ac = {r[0]: r for r in cur.fetchall()}

    with connections.open("procount", autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute("SELECT API_UWI, MERRICK_ID, ACTIVE_FLAG FROM pc.pc_completion")
        pc = {r[0]: r for r in cur.fetchall()}

    models: dict[str, Well] = {}
    for api, w in wm.items():
        a, p = ac.get(api), pc.get(api)
        if not a or not p:
            continue                      # not carried by every system yet
        gor, ngl_yield, wor = generator.model_params(api, w[7])
        models[api] = Well(
            api_uwi=api, well_name=w[1], well_number=w[2], operator_name="",
            operated=True, area=w[3], pad_name=w[4], central_facility=w[5],
            field_name="", county=w[6], state_code="TX", basin="", reservoir="",
            well_status=w[8], well_type=w[7],
            surface_lat=0, surface_lon=0, bh_lat=0, bh_lon=0,
            spud_date=None, completion_date=None, first_prod_date=w[9],
            lateral_ft=0, total_depth_ft=0, wi=0.0, nri=float(w[10]),
            merrick_id=p[1], propnum=a[1],
            qi_oil=float(a[2] or 0), di_nominal=float(a[3] or 0.7),
            b_factor=float(a[4] or 1.1),
            gor=gor, ngl_yield=ngl_yield, wor=wor,
            shut_in_from=(dt.date(2000, 1, 1)
                          if w[8] == generator.WELL_STATUS_SHUT_IN else None),
            reserve_cat=a[5] or "PDP",
        )
    return models


def _max_prod_date(cur) -> dt.date | None:
    return sqlutil.scalar(cur, "SELECT MAX(PROD_DATE) FROM pc.well_daily_prod")


# ---------------------------------------------------------------------------
# The individual activities
# ---------------------------------------------------------------------------
def advance_production_day(models: dict[str, Well], rng: random.Random, log) -> dict:
    """Pumpers report: one more day of allocated volumes in ProCount."""
    with connections.open("procount") as conn:
        cur = conn.cursor()
        last = _max_prod_date(cur)
        if last is None:
            return {"new_prod_days": 0, "new_prod_rows": 0}
        day = last + dt.timedelta(days=1)

        cur.execute("SELECT MERRICK_ID, API_UWI FROM pc.pc_completion WHERE ACTIVE_FLAG = 1")
        active = cur.fetchall()

        daily, downtime = [], []
        for merrick_id, api in active:
            w = models.get(api)
            if w is None or w.first_prod_date is None or day < w.first_prod_date:
                continue
            a = generator.actual_day(w, day, rng)
            daily.append((merrick_id, day, api, a["oil"], a["gas"], a["water"], a["ngl"],
                          round(a["oil"] * w.nri, 2), round(a["gas"] * w.nri, 2),
                          round(a["ngl"] * w.nri, 2),
                          a["producing_hours"], a["downtime_hours"], a["tubing"],
                          a["casing"], a["choke"], a["status"], "ESTIMATED"))
            if a["downtime"]:
                code, desc, hours, def_oil, def_gas = a["downtime"]
                downtime.append((merrick_id, day, 1, api, hours, code, desc, def_oil, def_gas))

        spec = registry.PC_DAILY
        sqlutil.executemany(cur,
            f"INSERT INTO {sqlutil.qualify(spec.schema, spec.name)} "
            f"({sqlutil.col_list(spec.columns)}) VALUES ({sqlutil.placeholders(len(spec.columns))})",
            daily)
        dspec = registry.PC_DOWNTIME
        sqlutil.executemany(cur,
            f"INSERT INTO {sqlutil.qualify(dspec.schema, dspec.name)} "
            f"({sqlutil.col_list(dspec.columns)}) VALUES ({sqlutil.placeholders(len(dspec.columns))})",
            downtime)

    log(f"  production day {day}: {len(daily):,} new rows, {len(downtime)} downtime events")
    return {"new_prod_days": 1, "new_prod_rows": len(daily),
            "new_downtime_rows": len(downtime), "prod_date": day.isoformat()}


def revise_allocations(rng: random.Random, n: int, log) -> dict:
    """Accounting closes the month: estimates firm up and volumes get restated.

    These are pure UPDATEs with no key change, which every strategy except FULL
    has to detect through its own mechanism.
    """
    with connections.open("procount") as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT TOP (?) MERRICK_ID, PROD_DATE, ALLOCATION_STATUS "
            "FROM pc.well_daily_prod WHERE ALLOCATION_STATUS <> 'FINAL' "
            "ORDER BY NEWID()", n)
        targets = cur.fetchall()
        promote = {"ESTIMATED": "ALLOCATED", "ALLOCATED": "FINAL"}
        for merrick_id, day, status in targets:
            factor = round(rng.uniform(0.93, 1.08), 4)
            cur.execute(
                "UPDATE pc.well_daily_prod SET "
                "OIL_BBL = ROUND(OIL_BBL * ?, 2), GAS_MCF = ROUND(GAS_MCF * ?, 2), "
                "NGL_BBL = ROUND(NGL_BBL * ?, 2), "
                "NET_OIL_BBL = ROUND(NET_OIL_BBL * ?, 2), "
                "NET_GAS_MCF = ROUND(NET_GAS_MCF * ?, 2), "
                "NET_NGL_BBL = ROUND(NET_NGL_BBL * ?, 2), "
                "ALLOCATION_STATUS = ?, UPDATED_TS = SYSUTCDATETIME() "
                "WHERE MERRICK_ID = ? AND PROD_DATE = ?",
                factor, factor, factor, factor, factor, factor,
                promote.get(status, "FINAL"), merrick_id, day)
    log(f"  allocation revisions: {len(targets)} daily rows restated")
    return {"allocation_revisions": len(targets)}


def edit_well_master(rng: random.Random, n: int, log) -> dict:
    """Land and operations edit the well header: shut-ins, returns, corrections."""
    shut_in = returned = edited = 0
    with connections.open("wellmaster") as conn:
        cur = conn.cursor()
        cur.execute("SELECT TOP (?) API_UWI, WELL_STATUS, PAD_NAME, WORKING_INTEREST "
                    "FROM wm.cct_well_master WHERE OPERATED_FLAG = 1 ORDER BY NEWID()", n)
        for api, status, pad, wi in cur.fetchall():
            roll = rng.random()
            if roll < 0.30 and status == generator.WELL_STATUS_PRODUCING:
                cur.execute("UPDATE wm.cct_well_master SET WELL_STATUS = ?, "
                            "UPDATED_TS = SYSUTCDATETIME() WHERE API_UWI = ?",
                            generator.WELL_STATUS_SHUT_IN, api)
                shut_in += 1
            elif roll < 0.55 and status == generator.WELL_STATUS_SHUT_IN:
                cur.execute("UPDATE wm.cct_well_master SET WELL_STATUS = ?, "
                            "UPDATED_TS = SYSUTCDATETIME() WHERE API_UWI = ?",
                            generator.WELL_STATUS_PRODUCING, api)
                returned += 1
            elif roll < 0.80:
                new_wi = round(min(float(wi) * rng.uniform(0.97, 1.03), 1.0), 6)
                cur.execute("UPDATE wm.cct_well_master SET WORKING_INTEREST = ?, "
                            "NET_REVENUE_INTEREST = ?, UPDATED_TS = SYSUTCDATETIME() "
                            "WHERE API_UWI = ?", new_wi, round(new_wi * 0.76, 6), api)
                edited += 1
            else:
                cur.execute("UPDATE wm.cct_well_master SET CENTRAL_FACILITY = ?, "
                            "UPDATED_TS = SYSUTCDATETIME() WHERE API_UWI = ?",
                            f"CTB-{rng.randint(1, 60):02d}", api)
                edited += 1

    # ProCount has to follow a shut-in, or the next day would still allocate volume.
    if shut_in or returned:
        with connections.open("procount") as conn, connections.open("wellmaster") as wmc:
            wcur = wmc.cursor()
            wcur.execute("SELECT API_UWI, WELL_STATUS FROM wm.cct_well_master "
                         "WHERE OPERATED_FLAG = 1")
            status_by_api = dict(wcur.fetchall())
            cur = conn.cursor()
            cur.execute("SELECT MERRICK_ID, API_UWI, ACTIVE_FLAG FROM pc.pc_completion")
            for merrick_id, api, active in cur.fetchall():
                want = 0 if status_by_api.get(api) == generator.WELL_STATUS_SHUT_IN else 1
                if want != active:
                    cur.execute("UPDATE pc.pc_completion SET ACTIVE_FLAG = ?, "
                                "UPDATED_TS = SYSUTCDATETIME() WHERE MERRICK_ID = ?",
                                want, merrick_id)

    log(f"  well master: {shut_in} shut in, {returned} returned to production, "
        f"{edited} attribute edits")
    return {"wells_shut_in": shut_in, "wells_returned": returned,
            "wellmaster_edits": edited}


def revise_forecast(rng: random.Random, n: int, log) -> dict:
    """Engineering re-runs type curves: a bulk UPDATE across many ARIES rows."""
    rows = 0
    with connections.open("aries") as conn:
        cur = conn.cursor()
        cur.execute("SELECT TOP (?) PROPNUM FROM ac.AC_PROPERTY ORDER BY NEWID()", n)
        propnums = [r[0] for r in cur.fetchall()]
        for propnum in propnums:
            factor = round(rng.uniform(0.85, 1.18), 4)
            effective = generator.today() + dt.timedelta(days=rng.randint(0, 30))
            cur.execute(
                "UPDATE ac.AC_DAILY SET "
                "GROSS_OIL_BBL = ROUND(GROSS_OIL_BBL * ?, 2), "
                "GROSS_GAS_MCF = ROUND(GROSS_GAS_MCF * ?, 2), "
                "GROSS_NGL_BBL = ROUND(GROSS_NGL_BBL * ?, 2), "
                "NET_OIL_BBL = ROUND(NET_OIL_BBL * ?, 2), "
                "NET_GAS_MCF = ROUND(NET_GAS_MCF * ?, 2), "
                "NET_NGL_BBL = ROUND(NET_NGL_BBL * ?, 2), "
                "UPDATED_TS = SYSUTCDATETIME() "
                "WHERE PROPNUM = ? AND D_DATE >= ?",
                factor, factor, factor, factor, factor, factor, propnum, effective)
            rows += max(cur.rowcount, 0)
            cur.execute("UPDATE ac.AC_PROPERTY SET QI_OIL_BOPD = ROUND(QI_OIL_BOPD * ?, 2), "
                        "EFFECTIVE_DATE = ?, UPDATED_TS = SYSUTCDATETIME() WHERE PROPNUM = ?",
                        factor, effective, propnum)
    log(f"  forecast: {len(propnums)} properties re-forecast, {rows:,} AC_DAILY rows updated")
    return {"forecast_properties": len(propnums), "forecast_rows": rows}


def bring_wells_online(rng: random.Random, n: int, log) -> dict:
    """Drilling delivers. A new well appears in all three systems at once, which
    is the case that shows an incremental load picking up genuinely new keys."""
    if n <= 0:
        return {"new_wells": 0}

    with connections.open("wellmaster", autocommit=True) as conn:
        cur = conn.cursor()
        used = {r[0] for r in cur.execute("SELECT API_UWI FROM wm.cct_well_master").fetchall()}
        next_merrick = 0
    with connections.open("procount", autocommit=True) as conn:
        next_merrick = int(sqlutil.scalar(
            conn.cursor(), "SELECT ISNULL(MAX(MERRICK_ID), 100000) FROM pc.pc_completion") or 100000)

    new_wells: list[Well] = []
    for i in range(n):
        county = rng.choice(generator.COUNTIES)
        while True:
            api = f"42{county.code}{rng.randint(80000, 99999):05d}0000"
            if api not in used:
                used.add(api)
                break
        w = generator.make_well(rng, operated=True, api=api, county=county)
        w.merrick_id = next_merrick + i + 1
        w.first_prod_date = generator.today()
        w.completion_date = w.first_prod_date - dt.timedelta(days=rng.randint(5, 20))
        w.spud_date = w.completion_date - dt.timedelta(days=rng.randint(35, 90))
        w.shut_in_from = None
        w.well_status = generator.WELL_STATUS_PRODUCING
        w.reserve_cat = "PDP"
        new_wells.append(w)

    from . import seeder
    with connections.open("wellmaster") as conn:
        spec = registry.WM_MASTER
        rows = [(w.api_uwi, w.well_name, w.well_number, w.operator_name, 1, w.area,
                 w.pad_name, w.central_facility, w.field_name, w.county, w.state_code,
                 w.basin, w.reservoir, w.well_status, w.well_type, w.surface_lat,
                 w.surface_lon, w.bh_lat, w.bh_lon, w.spud_date, w.completion_date,
                 w.first_prod_date, w.lateral_ft, w.total_depth_ft, w.wi, w.nri)
                for w in new_wells]
        sqlutil.executemany(conn.cursor(),
            f"INSERT INTO {sqlutil.qualify(spec.schema, spec.name)} "
            f"({sqlutil.col_list(spec.columns)}) VALUES ({sqlutil.placeholders(len(spec.columns))})",
            rows)

    with connections.open("procount") as conn:
        spec = registry.PC_COMPLETION
        rows = [(w.merrick_id, w.api_uwi, f"{w.well_name} {w.well_number}", w.prod_route,
                 w.battery_id, w.battery_name, w.area, 1, w.first_prod_date, "TEST")
                for w in new_wells]
        sqlutil.executemany(conn.cursor(),
            f"INSERT INTO {sqlutil.qualify(spec.schema, spec.name)} "
            f"({sqlutil.col_list(spec.columns)}) VALUES ({sqlutil.placeholders(len(spec.columns))})",
            rows)

    with connections.open("aries") as conn:
        cur = conn.cursor()
        pspec = registry.AC_PROPERTY
        prows = [(w.propnum, w.api_uwi, f"{w.well_name} {w.well_number}", w.well_name,
                  w.operator_name, w.area, w.pad_name, w.field_name, w.county,
                  w.state_code, w.reservoir, w.well_type, w.well_status,
                  w.first_prod_date, w.surface_lat, w.surface_lon, w.wi, w.nri,
                  "2026 BUDGET", w.reserve_cat, w.type_curve, w.first_prod_date,
                  w.qi_oil, w.di_nominal, w.b_factor) for w in new_wells]
        sqlutil.executemany(cur,
            f"INSERT INTO {sqlutil.qualify(pspec.schema, pspec.name)} "
            f"({sqlutil.col_list(pspec.columns)}) VALUES ({sqlutil.placeholders(len(pspec.columns))})",
            prows)

        dspec = registry.AC_DAILY
        drows = []
        for w in new_wells:
            for day in generator.date_range(w.first_prod_date, generator.FCST_END):
                oil, gas, water, ngl = generator.forecast_day(w, day)
                drows.append((w.propnum, day, w.api_uwi, f"{w.well_name} {w.well_number}",
                              w.area, w.pad_name, w.well_status, w.well_type,
                              oil, gas, water, ngl,
                              round(oil * w.nri, 2), round(gas * w.nri, 2),
                              round(ngl * w.nri, 2), "2026 BUDGET", w.reserve_cat, "BASE"))
        sqlutil.executemany(cur,
            f"INSERT INTO {sqlutil.qualify(dspec.schema, dspec.name)} "
            f"({sqlutil.col_list(dspec.columns)}) VALUES ({sqlutil.placeholders(len(dspec.columns))})",
            drows)

    log(f"  drilling: {len(new_wells)} new wells online "
        f"({', '.join(w.api_uwi for w in new_wells)}), {len(drows):,} forecast rows")
    return {"new_wells": len(new_wells), "new_forecast_rows": len(drows)}


def delete_bad_rows(rng: random.Random, n: int, log) -> dict:
    """Somebody deletes a mis-keyed record.

    This is the single change an UPDATED_TS watermark can never see, and the
    reason CT and CDC exist. FULL catches it because it re-reads everything; CT
    and CDC report it explicitly; INCREMENTAL never mentions it, so a pipeline
    built on one carries the row downstream forever.
    """
    deleted = {"deleted_prod_rows": 0, "deleted_downtime_rows": 0}
    if n <= 0:
        return deleted
    with connections.open("procount") as conn:
        cur = conn.cursor()
        cur.execute("SELECT TOP (?) MERRICK_ID, PROD_DATE FROM pc.well_daily_prod "
                    "ORDER BY NEWID()", n)
        for merrick_id, day in cur.fetchall():
            cur.execute("DELETE FROM pc.well_daily_prod WHERE MERRICK_ID = ? AND PROD_DATE = ?",
                        merrick_id, day)
            deleted["deleted_prod_rows"] += max(cur.rowcount, 0)
        cur.execute("SELECT TOP (?) MERRICK_ID, DOWNTIME_DATE, SEQ_NO "
                    "FROM pc.pc_daily_downtime ORDER BY NEWID()", max(1, n // 2))
        for merrick_id, day, seq in cur.fetchall():
            cur.execute("DELETE FROM pc.pc_daily_downtime WHERE MERRICK_ID = ? "
                        "AND DOWNTIME_DATE = ? AND SEQ_NO = ?", merrick_id, day, seq)
            deleted["deleted_downtime_rows"] += max(cur.rowcount, 0)
    log(f"  deletes: {deleted['deleted_prod_rows']} daily rows, "
        f"{deleted['deleted_downtime_rows']} downtime rows removed outright")
    return deleted


# ---------------------------------------------------------------------------
# One tick
# ---------------------------------------------------------------------------
ACTIVITIES = ("advance_day", "revise_allocations", "edit_wells", "revise_forecast",
              "new_wells", "deletes")


def simulate(scale: int = 1, activities=None, log=print) -> dict:
    """Run one operational cycle. Scale multiplies every activity except the
    production day, which is always exactly one day."""
    scale = max(1, min(int(scale), 20))
    activities = set(activities or ACTIVITIES)
    rng = random.Random()
    out: dict = {"scale": scale}

    log(f"Simulating {scale} cycle(s) of operations at {registry.COMPANY} ...")
    models = _load_models()
    if not models:
        log("  no operated wells found -- seed the databases first")
        return {"error": "no data; run a reset first"}

    if "advance_day" in activities:
        out.update(advance_production_day(models, rng, log))
    if "revise_allocations" in activities:
        out.update(revise_allocations(rng, 40 * scale, log))
    if "edit_wells" in activities:
        out.update(edit_well_master(rng, 8 * scale, log))
    if "revise_forecast" in activities:
        out.update(revise_forecast(rng, 3 * scale, log))
    if "new_wells" in activities:
        out.update(bring_wells_online(rng, 1 * scale, log))
    if "deletes" in activities:
        out.update(delete_bad_rows(rng, 3 * scale, log))

    from etl import runner
    runner.log_activity("", "CHURN", str(out)[:500],
                        sum(v for v in out.values() if isinstance(v, int)))
    log("Churn complete. Read the change feed to see which strategy catches what.")
    return out
