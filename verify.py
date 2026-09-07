#!/usr/bin/env python
"""End-to-end correctness harness.

Runs the exercise the app exists to teach and asserts the outcome, so a change to
the extractors cannot quietly break the lesson:

    1. every source connects and holds the expected shape
    2. the seeded data is internally consistent (hours, interests, API format,
       operated-only scoping)
    3. FULL reads everything, every time
    4. INCREMENTAL reads nothing when nothing changed, then exactly the churn
    5. INCREMENTAL cannot see a hard delete   <- the point of the whole thing
    6. CT sees the same change AND the delete
    7. disabling a mechanism invalidates its watermark
    8. every deployed column has a description

    python verify.py            # skips CDC unless it is already enabled
    python verify.py --cdc      # also exercises CDC (needs sysadmin to have run
                                #   sys.sp_cdc_enable_db, plus SQL Server Agent)
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

from core import connections, registry, sqlutil          # noqa: E402
from core.models import ensure_defaults                  # noqa: E402
from etl import runner, tracking, watermark              # noqa: E402
from systems import churn                                # noqa: E402

PASS, FAIL = "  ok  ", " FAIL "
_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    print(f"[{PASS if condition else FAIL}] {name}" + (f"  -- {detail}" if detail else ""))
    if not condition:
        _failures.append(name)
    return condition


def section(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 66 - len(title)))


# ---------------------------------------------------------------------------
def check_connections() -> None:
    section("connections")
    for key in registry.SOURCE_SYSTEMS:
        r = connections.test(key)
        check(f"{key} connects", r["ok"], r.get("error") or
              f"{r.get('database')} as {r.get('login')} in {r['elapsed_ms']} ms")
        if r["ok"]:
            check(f"{key} is db_owner", r["is_db_owner"],
                  "needed for ALTER DATABASE ... SET CHANGE_TRACKING")


def check_shape() -> None:
    section("schema shape")
    for key, spec in registry.SOURCE_SYSTEMS.items():
        with connections.open(key, autocommit=True) as conn:
            cur = conn.cursor()
            for t in spec.tables:
                check(f"{t.qualified} exists",
                      sqlutil.table_exists(cur, t.schema, t.name))
            n = sqlutil.scalar(cur, """
                SELECT COUNT(*) FROM sys.foreign_keys fk
                  JOIN sys.schemas s ON s.schema_id = fk.schema_id WHERE s.name = ?""",
                spec.schema)
            check(f"{spec.schema} has no foreign keys", n == 0, f"{n} found")


def check_data() -> None:
    section("data consistency")
    with connections.open("wellmaster", autocommit=True) as conn:
        cur = conn.cursor()
        total = sqlutil.scalar(cur, "SELECT COUNT(*) FROM wm.cct_well_master")
        operated = sqlutil.scalar(
            cur, "SELECT COUNT(*) FROM wm.cct_well_master WHERE OPERATED_FLAG = 1")
        check("well master row count", total >= registry.OPERATED_WELLS +
              registry.NON_OPERATED_WELLS, f"{total:,} wells")
        check("operated well count", operated >= registry.OPERATED_WELLS,
              f"{operated:,} operated")
        bad_api = sqlutil.scalar(cur, """
            SELECT COUNT(*) FROM wm.cct_well_master
             WHERE LEN(API_UWI) <> 14 OR LEFT(API_UWI, 2) <> '42'""")
        check("every API is 14 chars and starts with 42 (Texas)", bad_api == 0,
              f"{bad_api} bad")
        bad_nri = sqlutil.scalar(cur, """
            SELECT COUNT(*) FROM wm.cct_well_master
             WHERE NET_REVENUE_INTEREST > WORKING_INTEREST
                OR NET_REVENUE_INTEREST <= 0 OR WORKING_INTEREST > 1""")
        check("NRI is between 0 and the working interest", bad_nri == 0, f"{bad_nri} bad")
        operated_apis = {r[0] for r in cur.execute(
            "SELECT API_UWI FROM wm.cct_well_master WHERE OPERATED_FLAG = 1").fetchall()}
        nonop_apis = {r[0] for r in cur.execute(
            "SELECT API_UWI FROM wm.cct_well_master WHERE OPERATED_FLAG = 0").fetchall()}

    with connections.open("procount", autocommit=True) as conn:
        cur = conn.cursor()
        bad_hours = sqlutil.scalar(cur, """
            SELECT COUNT(*) FROM pc.well_daily_prod
             WHERE ABS(PRODUCING_HOURS + DOWNTIME_HOURS - 24) > 0.01""")
        check("producing + downtime hours = 24 on every daily row", bad_hours == 0,
              f"{bad_hours} bad")
        early = sqlutil.scalar(cur, """
            SELECT COUNT(*) FROM pc.well_daily_prod d
              JOIN pc.pc_completion c ON c.MERRICK_ID = d.MERRICK_ID
             WHERE d.PROD_DATE < c.FIRST_PROD_DATE""")
        check("no production before first production date", early == 0, f"{early} bad")
        bad_net = sqlutil.scalar(cur, """
            SELECT COUNT(*) FROM pc.well_daily_prod
             WHERE NET_OIL_BBL > OIL_BBL + 0.01 OR NET_GAS_MCF > GAS_MCF + 0.01""")
        check("net volumes never exceed gross", bad_net == 0, f"{bad_net} bad")
        pc_apis = {r[0] for r in cur.execute(
            "SELECT DISTINCT API_UWI FROM pc.pc_completion").fetchall()}
        zero_days = sqlutil.scalar(cur, """
            SELECT COUNT(*) FROM pc.well_daily_prod WHERE OIL_BBL = 0 AND GAS_MCF = 0""")
        check("shut-in wells carry explicit zero rows", zero_days > 0,
              f"{zero_days:,} zero-volume days")

    with connections.open("aries", autocommit=True) as conn:
        cur = conn.cursor()
        ac_apis = {r[0] for r in cur.execute(
            "SELECT DISTINCT API_UWI FROM ac.AC_PROPERTY").fetchall()}
        span = cur.execute("SELECT MIN(D_DATE), MAX(D_DATE) FROM ac.AC_DAILY").fetchone()
        check("forecast covers calendar 2026",
              str(span[0]) == registry.PRODUCTION_START and
              str(span[1]) == registry.FORECAST_END, f"{span[0]} .. {span[1]}")

    check("ProCount carries only operated wells", pc_apis <= operated_apis,
          f"{len(pc_apis - operated_apis)} stray")
    check("ARIES carries only operated wells", ac_apis <= operated_apis,
          f"{len(ac_apis - operated_apis)} stray")
    check("no non-operated well reached ProCount",
          not (pc_apis & nonop_apis), f"{len(pc_apis & nonop_apis)} leaked")


# ---------------------------------------------------------------------------
def _read(system: str, table_name: str, strategy: str, commit: bool = True) -> dict:
    spec = registry.system(system).table(table_name)
    with connections.open(system, autocommit=True) as conn:
        return runner.read_table(conn.cursor(), system, spec, strategy, commit)


def check_full() -> None:
    section("FULL")
    runner.switch_mode("procount", "FULL")
    a = _read("procount", "well_daily_prod", "FULL")
    b = _read("procount", "well_daily_prod", "FULL")
    check("FULL reads every row", a["read"] > 100_000, f"{a['read']:,} rows")
    check("FULL reads the same count twice", a["read"] == b["read"],
          f"{a['read']:,} then {b['read']:,}")
    check("FULL never reports deletes separately", a["deleted"] == 0,
          "a wipe-and-reload has no delete concept")


def check_incremental() -> None:
    section("INCREMENTAL -- and what it cannot see")
    runner.switch_mode("procount", "INCREMENTAL")

    base = _read("procount", "well_daily_prod", "INCREMENTAL")
    check("first INCREMENTAL run baselines", base["baseline"], f"{base['read']:,} rows")

    quiet = _read("procount", "well_daily_prod", "INCREMENTAL")
    check("INCREMENTAL reads nothing when nothing changed", quiet["read"] == 0,
          f"{quiet['read']} rows")

    import random
    churn.revise_allocations(random.Random(), 25, lambda _m: None)
    after = _read("procount", "well_daily_prod", "INCREMENTAL")
    check("INCREMENTAL picks up updates", after["read"] > 0, f"{after['read']} rows")

    # The lesson: delete rows, then confirm the watermark strategy is blind to it.
    with connections.open("procount", autocommit=True) as conn:
        cur = conn.cursor()
        before = sqlutil.scalar(cur, "SELECT COUNT_BIG(*) FROM pc.well_daily_prod")
        cur.execute("DELETE FROM pc.well_daily_prod WHERE MERRICK_ID IN "
                    "(SELECT TOP 5 MERRICK_ID FROM pc.well_daily_prod ORDER BY NEWID()) "
                    "AND PROD_DATE = (SELECT MAX(PROD_DATE) FROM pc.well_daily_prod)")
        deleted = cur.rowcount
        now = sqlutil.scalar(cur, "SELECT COUNT_BIG(*) FROM pc.well_daily_prod")
    check("rows really were deleted from the source", before - now == deleted > 0,
          f"{deleted} rows gone")

    blind = _read("procount", "well_daily_prod", "INCREMENTAL")
    check("INCREMENTAL is blind to hard deletes",
          blind["read"] == 0 and blind["deleted"] == 0,
          f"{deleted} rows vanished and the strategy reported {blind['read']} changes")


def check_ct() -> None:
    section("CT -- Change Tracking")
    runner.switch_mode("wellmaster", "CT")
    with connections.open("wellmaster", autocommit=True) as conn:
        st = tracking.state(conn.cursor(), "wellmaster")
    check("CT is on at database level", st["db_change_tracking"])
    check("CT is on for cct_well_master", st["tables"][0]["ct"])

    base = _read("wellmaster", "cct_well_master", "CT")
    check("first CT run baselines", base["baseline"], f"{base['read']:,} rows")

    quiet = _read("wellmaster", "cct_well_master", "CT")
    check("CT reads nothing when nothing changed", quiet["read"] == 0,
          f"{quiet['read']} rows")

    with connections.open("wellmaster", autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute("UPDATE TOP (7) wm.cct_well_master SET CENTRAL_FACILITY = 'CTB-99', "
                    "UPDATED_TS = SYSUTCDATETIME()")
        updated = cur.rowcount
        cur.execute("DELETE FROM wm.cct_well_master WHERE API_UWI IN "
                    "(SELECT TOP 3 API_UWI FROM wm.cct_well_master WHERE OPERATED_FLAG = 0 "
                    " ORDER BY NEWID())")
        removed = cur.rowcount

    caught = _read("wellmaster", "cct_well_master", "CT")
    check("CT sees the updates", caught["read"] == updated,
          f"{updated} updated, CT read {caught['read']}")
    check("CT sees the deletes", caught["deleted"] == removed,
          f"{removed} deleted, CT reported {caught['deleted']}")


def check_watermark_invalidation() -> None:
    section("disabling a mechanism must invalidate its watermark")
    runner.switch_mode("wellmaster", "CT")
    _read("wellmaster", "cct_well_master", "CT")
    spec = registry.WM_MASTER
    check("CT watermark exists after a run",
          watermark.read("wellmaster", spec, "CT").get("ct_version") is not None)

    runner.switch_mode("wellmaster", "FULL")           # this disarms CT
    check("switching away drops the CT watermark",
          watermark.read("wellmaster", spec, "CT") == {},
          "otherwise the next CT run trusts a version that no longer covers reality")
    with connections.open("wellmaster", autocommit=True) as conn:
        st = tracking.state(conn.cursor(), "wellmaster")
    check("CT is off on the table after switching away", not st["tables"][0]["ct"])


def check_untracked_table_rebaselines() -> None:
    """A rebuild drops and recreates the source tables, which silently takes
    table-level tracking off while the database-level version keeps advancing.
    The stored watermark then names a version no table can answer for."""
    section("a table that lost its tracking must re-baseline, not raise")
    spec = registry.WM_MASTER
    runner.switch_mode("wellmaster", "CT")
    _read("wellmaster", "cct_well_master", "CT")
    check("CT watermark exists after a run",
          watermark.read("wellmaster", spec, "CT").get("ct_version") is not None)

    # Take tracking off behind the app's back -- exactly what dropping and
    # recreating the table during a rebuild does.
    with connections.open("wellmaster", autocommit=True) as conn:
        cur = conn.cursor()
        tracking.disable_table_ct(cur, spec)
        check("MIN_VALID_VERSION is NULL once the table is untracked",
              tracking.ct_min_valid_version(cur, spec) is None,
              "a staleness check that only compares versions cannot see this")

    after = None
    try:
        after = _read("wellmaster", "cct_well_master", "CT")
        ok, detail = True, f"{after['read']:,} rows"
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"[:140]
    check("an untracked table re-baselines instead of raising", ok, detail)
    if after is not None:
        check("and the run is recorded as a baseline", after["baseline"])

    runner.switch_mode("wellmaster", "CT")
    with connections.open("wellmaster", autocommit=True) as conn:
        st = tracking.state(conn.cursor(), "wellmaster")
    check("re-arming puts CT back on the table", st["tables"][0]["ct"])


def check_arm_is_verified() -> None:
    """Arming must be read back from the server, so a mode switch cannot report
    success while the source is untracked."""
    section("arming is verified against the server, not assumed")
    runner.switch_mode("wellmaster", "CT")
    with connections.open("wellmaster", autocommit=True) as conn:
        cur = conn.cursor()
        notes = tracking.verify_armed(cur, "wellmaster", "CT")
    check("verify_armed confirms CT", any("verified" in n for n in notes),
          "; ".join(notes)[:120])

    with connections.open("wellmaster", autocommit=True) as conn:
        cur = conn.cursor()
        tracking.disable_table_ct(cur, registry.WM_MASTER)
        raised = False
        try:
            tracking.verify_armed(cur, "wellmaster", "CT")
        except Exception:
            raised = True
        check("verify_armed refuses to call an untracked table armed", raised)
        tracking.enable_table_ct(cur, registry.WM_MASTER)

    runner.switch_mode("wellmaster", "FULL")


def check_cdc(enabled_only: bool) -> None:
    section("CDC -- Change Data Capture")
    with connections.open("procount", autocommit=True) as conn:
        on = tracking.cdc_database_on(conn.cursor())
    if not on and enabled_only:
        print("  skipped -- CDC is not enabled on PROCOUNT_DB.")
        print("  A sysadmin must run once:  USE [PROCOUNT_DB]; EXEC sys.sp_cdc_enable_db;")
        print("  Then re-run with --cdc.")
        return
    try:
        runner.switch_mode("procount", "CDC")
    except Exception as exc:
        check("CDC can be armed", False, str(exc)[:160])
        return
    with connections.open("procount", autocommit=True) as conn:
        healthy, msg = tracking.cdc_capture_healthy(conn.cursor())
    check("CDC capture is healthy", healthy, msg)

    base = _read("procount", "pc_completion", "CDC")
    check("first CDC run baselines", base["baseline"], f"{base['read']:,} rows")
    with connections.open("procount", autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute("UPDATE TOP (4) pc.pc_completion SET PROD_ROUTE = 'RTE-VERIFY', "
                    "UPDATED_TS = SYSUTCDATETIME()")
        updated = cur.rowcount
    import time
    time.sleep(6)                       # let the capture job drain the log
    caught = _read("procount", "pc_completion", "CDC")
    check("CDC sees the updates", caught["read"] >= 1,
          f"{updated} updated, CDC read {caught['read']}")


def check_docs() -> None:
    section("documentation")
    from documentation import catalog

    missing = catalog.undocumented()
    check("every deployed column has a description", not missing,
          f"{len(missing)} undocumented: {', '.join(missing[:6])}")
    svg = catalog.erd_svg(catalog.row_counts())
    check("ERD renders", svg.startswith("<svg") and svg.endswith("</svg>"),
          f"{len(svg):,} bytes")
    d = catalog.data_dictionary()
    check("data dictionary covers all three systems",
          all(not c["error"] and c["tables"] for c in d.values()))


def check_transformations() -> None:
    """The Transformations page ships SQL people copy and run, so the harness
    runs it too. A lesson that no longer executes against the deployed schema is
    a broken lesson, and a column rename is exactly how that happens."""
    section("transformations")
    import re as _re

    from core.models import profile
    from core.views import _reject_reason
    from documentation import transformations as tx

    sections = tx.render(lambda k: profile(k).database)
    lessons = [l for s in sections for l in s["lessons"]]
    check("lessons are defined", len(lessons) > 0, f"{len(lessons)} lessons")

    slugs = [l["slug"] for l in lessons]
    dupes = {s for s in slugs if slugs.count(s) > 1}
    check("lesson slugs are unique", not dupes, ", ".join(sorted(dupes)))

    unexpanded = [(l["slug"], m) for l in lessons for d in ("tsql", "snowflake")
                  for m in _re.findall(r"{[^}]*}", l[d])]
    check("every table token expands", not unexpanded, str(unexpanded[:4]))

    bad_meta = [l["slug"] for l in lessons
                if l["system"] not in registry.SOURCE_SYSTEMS or l["level"] not in tx.LEVELS]
    check("every lesson names a real system and level", not bad_meta,
          ", ".join(bad_meta[:6]))

    both = [l["slug"] for l in lessons if not l["tsql"].strip() or not l["snowflake"].strip()]
    check("every lesson carries both dialects", not both, ", ".join(both[:6]))

    # The scratchpad guard has to agree with the runnable flag in both directions:
    # a lesson offered with a Run button that the guard would refuse is a dead
    # button, and a write statement the guard would accept is a much worse bug.
    guard = [(l["slug"], _reject_reason(l["tsql"])) for l in lessons]
    dead = [s for (s, why), l in zip(guard, lessons) if l["runnable"] and why]
    check("every runnable lesson passes the read-only guard", not dead,
          ", ".join(dead[:6]))
    leaks = [s for (s, why), l in zip(guard, lessons) if not l["runnable"] and not why]
    check("every write lesson is refused by the guard", not leaks, ", ".join(leaks[:6]))

    failed, empty = [], []
    for l in lessons:
        if not l["runnable"]:
            continue
        try:
            with connections.open(l["system"], autocommit=True, timeout=30) as conn:
                cur = conn.cursor()
                cur.execute(l["tsql"])
                if not cur.fetchmany(1):
                    empty.append(l["slug"])
        except Exception as exc:
            failed.append(f"{l['slug']}: {type(exc).__name__}")
    check("every runnable lesson executes on the source", not failed,
          "; ".join(failed[:4]))
    # Empty is not a failure -- the reconciliation lessons return nothing when the
    # three systems agree, which is the answer they are asking for.
    if empty:
        print(f"      note: {len(empty)} lesson(s) returned no rows: {', '.join(empty)}")


# ---------------------------------------------------------------------------
def main() -> int:
    ensure_defaults()
    print(f"CCT Oil & Cattle -- verification harness\n")
    check_connections()
    check_shape()
    check_data()
    check_full()
    check_incremental()
    check_ct()
    check_watermark_invalidation()
    check_untracked_table_rebaselines()
    check_arm_is_verified()
    check_cdc(enabled_only="--cdc" not in sys.argv)
    check_docs()
    check_transformations()

    print("\n" + "=" * 72)
    if _failures:
        print(f"{len(_failures)} check(s) FAILED:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
