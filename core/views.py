"""Pages and JSON endpoints.

One Django app hosts every system. The navbar links to a page per application,
each showing its own connection, its own tables, its own row counts and its own
records -- but they are three separate databases behind three separate connection
profiles, which is the whole point.

There is no warehouse. ADF or Fivetran will move these tables to Snowflake; what
this app owns is the source side -- realistic data, a live simulator, the change
mechanism an ingestion tool needs, and the documentation to hand over.
"""
from __future__ import annotations

import datetime as dt
import re
import time

from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from etl import runner, tracking, watermark

from . import connections, dialects, jobs, registry, sqlutil
from .models import ConnectionProfile, ensure_defaults, profile

PAGE_SIZE = 50

# Columns a free-text search will look through, per table.
SEARCHABLE = {
    "cct_well_master": ("API_UWI", "WELL_NAME", "AREA", "PAD_NAME", "COUNTY",
                        "OPERATOR_NAME", "WELL_STATUS", "CENTRAL_FACILITY"),
    "pc_completion": ("API_UWI", "COMPLETION_NAME", "AREA", "BATTERY_NAME", "PROD_ROUTE"),
    "well_daily_prod": ("API_UWI", "WELL_STATUS_CODE", "ALLOCATION_STATUS"),
    "pc_daily_downtime": ("API_UWI", "REASON_CODE", "REASON_DESC"),
    "AC_PROPERTY": ("PROPNUM", "API_UWI", "WELL_NAME", "AREA", "COUNTY", "RESERVE_CAT"),
    "AC_DAILY": ("PROPNUM", "API_UWI", "WELL_NAME", "AREA", "WELL_STATUS"),
}


def _nav(active: str = "") -> dict:
    ensure_defaults()
    return {
        "active": active,
        "company": registry.COMPANY,
        "systems": list(registry.SOURCE_SYSTEMS.values()),
        "ticker": jobs.ticker.status(),
    }


def _json_safe(v):
    # datetime before date -- datetime is a subclass of date, and date.isoformat
    # takes no separator argument.
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=" ", timespec="milliseconds")
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if hasattr(v, "quantize"):                 # Decimal
        return float(v)
    return v


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
def dashboard(request):
    return render(request, "core/dashboard.html", _nav("dashboard"))


def api_overview(request):
    """Everything the dashboard shows, in one call."""
    modes = runner.all_modes()
    cards = []
    for key, spec in registry.SOURCE_SYSTEMS.items():
        prof = profile(key)
        card = {"key": key, "label": spec.label, "vendor": spec.vendor,
                "blurb": spec.blurb, "database": prof.database,
                "target": prof.describe(), "db_type": prof.dialect.label,
                "mode": modes.get(key, "FULL"), "ok": False, "tables": []}
        try:
            with connections.open(key, autocommit=True) as conn:
                card.update(tracking.state(conn.cursor(), key), ok=True)
        except Exception as exc:
            card["error"] = f"{type(exc).__name__}: {exc}"
        cards.append(card)

    return JsonResponse({"ok": True, "systems": cards,
                         "ticker": jobs.ticker.status(),
                         "activity": runner.recent_activity(12),
                         "company": _company_summary()})


def _company_summary() -> dict:
    """Headline numbers, the way an operations morning report would open."""
    out: dict = {}
    try:
        with connections.open("wellmaster", autocommit=True) as conn:
            cur = conn.cursor()
            if sqlutil.table_exists(cur, "wm", "cct_well_master"):
                cur.execute("SELECT COUNT(*), SUM(CASE WHEN OPERATED_FLAG = 1 THEN 1 ELSE 0 END), "
                            "SUM(CASE WHEN OPERATED_FLAG = 1 AND WELL_STATUS = 'SHUT-IN' "
                            "THEN 1 ELSE 0 END) FROM wm.cct_well_master")
                total, operated, shut_in = cur.fetchone()
                out.update(wells=total, operated=operated,
                           non_operated=(total or 0) - (operated or 0), shut_in=shut_in)
    except Exception:
        pass
    try:
        with connections.open("procount", autocommit=True) as conn:
            cur = conn.cursor()
            if sqlutil.table_exists(cur, "pc", "well_daily_prod"):
                last = sqlutil.scalar(cur, "SELECT MAX(PROD_DATE) FROM pc.well_daily_prod")
                out["last_prod_date"] = last.isoformat() if last else None
                if last:
                    cur.execute("SELECT SUM(OIL_BBL), SUM(GAS_MCF), SUM(NGL_BBL), "
                                "SUM(WATER_BBL), COUNT(*) FROM pc.well_daily_prod "
                                "WHERE PROD_DATE = ?", last)
                    oil, gas, ngl, water, wells = cur.fetchone()
                    out.update(last_oil=float(oil or 0), last_gas=float(gas or 0),
                               last_ngl=float(ngl or 0), last_water=float(water or 0),
                               last_wells=wells,
                               last_boe=float(oil or 0) + float(ngl or 0) + float(gas or 0) / 6.0)
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# Per-application pages
# ---------------------------------------------------------------------------
def system_detail(request, key: str):
    if key not in registry.SOURCE_SYSTEMS:
        raise Http404(key)
    spec = registry.SOURCE_SYSTEMS[key]
    ctx = _nav(key)
    ctx.update(system=spec, profile=profile(key), strategies=registry.STRATEGIES,
               table_names=[t.name for t in spec.tables])
    return render(request, "core/system.html", ctx)


def api_system_status(request, key: str):
    prof = profile(key)
    out = {"system": key, "target": prof.describe(), "db_type": prof.dialect.label,
           "capabilities": prof.dialect.capabilities(), "mode": runner.get_mode(key),
           "ok": False}
    try:
        with connections.open(key, autocommit=True) as conn:
            out.update(tracking.state(conn.cursor(), key), ok=True)
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return JsonResponse(out)


# ---------------------------------------------------------------------------
# Record browser
# ---------------------------------------------------------------------------
def _resolve_table(system_key: str, table_name: str):
    spec = registry.system(system_key)
    for t in spec.tables:
        if t.name == table_name:
            return t
    raise Http404(table_name)


def api_records(request, key: str, table: str):
    """Paged records for any table in any system. Read-only."""
    spec = _resolve_table(key, table)
    page = max(1, int(request.GET.get("page", 1)))
    size = min(max(int(request.GET.get("size", PAGE_SIZE)), 1), 500)
    q = (request.GET.get("q") or "").strip()

    where, params = "", []
    if q:
        cols = SEARCHABLE.get(spec.name)
        if cols:
            where = "WHERE " + " OR ".join(f"{sqlutil.ident(c)} LIKE ?" for c in cols)
            params = [f"%{q}%"] * len(cols)

    try:
        with connections.open(key, autocommit=True) as conn:
            cur = conn.cursor()
            if not sqlutil.table_exists(cur, spec.schema, spec.name):
                return JsonResponse({"ok": False,
                                     "error": f"{spec.qualified} does not exist yet -- "
                                              "rebuild the databases from the dashboard"},
                                    status=409)
            qualified = sqlutil.qualify(spec.schema, spec.name)
            total = int(sqlutil.scalar(cur, f"SELECT COUNT_BIG(*) FROM {qualified} {where}",
                                       *params) or 0)
            order = ", ".join(sqlutil.ident(c) for c in spec.pk)
            cur.execute(
                f"SELECT * FROM {qualified} {where} ORDER BY {order} "
                f"OFFSET {(page - 1) * size} ROWS FETCH NEXT {size} ROWS ONLY", *params)
            columns = [c[0] for c in cur.description]
            rows = [[_json_safe(v) for v in r] for r in cur.fetchall()]
    except Http404:
        raise
    except Exception as exc:
        return JsonResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)

    return JsonResponse({"ok": True, "table": spec.qualified, "columns": columns,
                         "rows": rows, "total": total, "page": page, "size": size,
                         "pages": max(1, -(-total // size)), "pk": list(spec.pk)})


# ---------------------------------------------------------------------------
# Connections page
# ---------------------------------------------------------------------------
def connections_page(request):
    ensure_defaults()
    if request.method == "POST":
        for prof in ConnectionProfile.objects.all():
            p = f"{prof.system_key}__"
            if f"{p}server" not in request.POST:
                continue
            prof.db_type = request.POST.get(f"{p}db_type", prof.db_type)
            prof.server = request.POST.get(f"{p}server", prof.server).strip()
            prof.port = int(request.POST.get(f"{p}port") or prof.port)
            prof.database = request.POST.get(f"{p}database", prof.database).strip()
            prof.username = request.POST.get(f"{p}username", prof.username).strip()
            pwd = request.POST.get(f"{p}password", "")
            if pwd:                                   # blank means "leave as is"
                prof.password = pwd
            prof.extra_options = request.POST.get(f"{p}extra_options", "").strip()
            prof.save()
        return redirect("connections")

    ctx = _nav("connections")
    ctx.update(profiles=ConnectionProfile.objects.all(),
               dialects=list(dialects.DIALECTS.values()))
    return render(request, "core/connections.html", ctx)


def api_test_connection(request, key: str):
    return JsonResponse(connections.test(key))


# ---------------------------------------------------------------------------
# Change feed console
# ---------------------------------------------------------------------------
def etl_console(request):
    ctx = _nav("etl")
    ctx.update(strategies=registry.STRATEGIES,
               systems=list(registry.SOURCE_SYSTEMS.values()))
    return render(request, "core/etl.html", ctx)


def api_etl_status(request):
    out = {"ok": True, "modes": runner.all_modes(),
           "watermarks": watermark.all_rows(),
           "runs": runner.recent_runs(60),
           "activity": runner.recent_activity(20),
           "ticker": jobs.ticker.status()}
    return JsonResponse(out)


# ---------------------------------------------------------------------------
# Actions -- all of them run as background jobs and are polled
# ---------------------------------------------------------------------------
def _job_response(job) -> JsonResponse:
    return JsonResponse({"ok": True, "job": job.snapshot()})


@require_POST
def api_reset(request):
    from systems import seeder

    drop = request.POST.get("drop") == "1"

    def work(log):
        if drop:
            for key in reversed(list(registry.SOURCE_SYSTEMS)):
                seeder.drop_schema(key, log)
        result = seeder.seed_all(log=log, progress=log)
        watermark.clear("wellmaster"); watermark.clear("procount"); watermark.clear("aries")
        runner.log_activity("", "RESET", str(result)[:500],
                            sum(v for v in result.values() if isinstance(v, int)))
        log("Watermarks cleared. Every system will baseline on its next read.")
        # Recreated tables come back untracked, so anything still recorded as CT
        # or CDC has to be armed again before it is read.
        log("Re-arming change mechanisms ...")
        runner.rearm_all(log)
        return result

    return _job_response(jobs.start("reset", work))


@require_POST
def api_churn(request):
    from systems import churn

    scale = int(request.POST.get("scale") or 1)
    activities = request.POST.getlist("activity") or None

    def work(log):
        return churn.simulate(scale, activities, log=log)

    return _job_response(jobs.start("churn", work))


@require_POST
def api_mode(request):
    key = request.POST.get("system")
    strategy = request.POST.get("strategy")
    rebuild = request.POST.get("rebuild") == "1"

    def work(log):
        log(f"Arming {key} for {strategy} ...")
        result = runner.switch_mode(key, strategy, rebuild=rebuild, log=log)
        log(f"{key}: {result['previous']} -> {result['strategy']}")
        return result

    return _job_response(jobs.start("mode", work))


@require_POST
def api_run_etl(request):
    key = request.POST.get("system") or ""
    commit = request.POST.get("commit", "1") == "1"

    def work(log):
        if key and key in registry.SOURCE_SYSTEMS:
            return runner.run_system(key, commit=commit, log=log)
        return runner.run_all(commit=commit, log=log)

    return _job_response(jobs.start("read", work))


@require_POST
def api_ticker(request):
    action = request.POST.get("action")
    if action == "start":
        jobs.ticker.start(interval=int(request.POST.get("interval") or 60),
                          scale=int(request.POST.get("scale") or 1),
                          run_etl=request.POST.get("run_etl") == "1")
    elif action == "stop":
        jobs.ticker.stop()
    return JsonResponse({"ok": True, "ticker": jobs.ticker.status()})


def api_job(request, job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return JsonResponse({"ok": False, "error": "unknown job"}, status=404)
    return JsonResponse({"ok": True, "job": job.snapshot(int(request.GET.get("since", 0)))})


# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------
def docs(request):
    from documentation import catalog, joins

    dictionary = catalog.data_dictionary()
    counts = {f"{cat['schema']}.{t}": meta["rows"]
              for cat in dictionary.values() for t, meta in cat["tables"].items()}

    ctx = _nav("docs")
    ctx.update(systems=list(registry.SOURCE_SYSTEMS.values()),
               dictionary=dictionary,
               erd_svg=catalog.erd_svg(counts),
               edges=catalog.EDGE_NOTES,
               joins=joins.JOIN_RECIPES,
               ingestion=joins.INGESTION_NOTES)
    return render(request, "core/docs.html", ctx)


# ---------------------------------------------------------------------------
# Transformations -- the SQL curriculum, and the scratchpad that runs it
# ---------------------------------------------------------------------------
def transformations(request):
    from documentation import transformations as tx

    def database_for(key: str) -> str:
        return profile(key).database

    ctx = _nav("transformations")
    ctx.update(sections=tx.render(database_for),
               levels=tx.LEVELS,
               run_targets=[{"key": k, "label": s.label,
                             "database": profile(k).database}
                            for k, s in registry.SOURCE_SYSTEMS.items()],
               default_target="procount")
    return render(request, "core/transformations.html", ctx)


# The scratchpad executes SQL a user typed, which is the one place in this app
# where that is true. Two independent guards, because either alone is a bad bet:
#   1. the statement is parsed far enough to prove it is a single read, and
#   2. it runs inside a transaction that is always rolled back.
QUERY_ROW_CAP = 1000
QUERY_TIMEOUT_S = 45

_WRITE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|merge|into|exec|execute"
    r"|grant|revoke|deny|backup|restore|shutdown|reconfigure|kill|checkpoint"
    r"|dbcc|openrowset|openquery|opendatasource|openjson|bulk|waitfor"
    r"|writetext|updatetext|readtext|sp_[a-z0-9_]*|xp_[a-z0-9_]*)\b",
    re.IGNORECASE)


def _scrub(sql: str) -> str:
    """Blank out string literals, line comments and block comments.

    Only the scrubbed copy is inspected; the original is what executes. A word
    inside a comment or a literal must not trip the guard, and a keyword must
    not be able to hide behind either.
    """
    out, i, n = [], 0, len(sql)
    while i < n:
        two = sql[i:i + 2]
        if sql[i] == "'":                       # string literal -> ''
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if sql[j:j + 2] == "''":
                        j += 2
                        continue
                    break
                j += 1
            out.append("''")
            i = j + 1
        elif sql[i] == "[":                     # bracketed identifier -> keep as a word
            j = sql.find("]", i)
            j = n if j < 0 else j
            out.append(sql[i:j + 1])
            i = j + 1
        elif two == "--":
            j = sql.find("\n", i)
            i = n if j < 0 else j
        elif two == "/*":
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        else:
            out.append(sql[i])
            i += 1
    return "".join(out)


def _reject_reason(sql: str) -> str | None:
    """None if this is a single read-only statement, else why it was refused."""
    scrubbed = _scrub(sql)
    statements = [s for s in scrubbed.split(";") if s.strip()]
    if not statements:
        return "nothing to run"
    if len(statements) > 1:
        return ("the scratchpad runs one statement at a time -- "
                f"this is {len(statements)}")
    body = statements[0].strip()
    if not re.match(r"^(select|with)\b", body, re.IGNORECASE):
        return "only SELECT and WITH statements can be run here"
    found = _WRITE_KEYWORDS.search(body)
    if found:
        return (f"'{found.group(0).upper()}' is not allowed -- the scratchpad reads, "
                "it never writes. Copy the SQL into a real client to run it.")
    return None


@require_POST
def api_query(request):
    """Run one read-only statement against one source connection and return a grid."""
    key = request.POST.get("system") or "procount"
    if key not in registry.SOURCE_SYSTEMS:
        return JsonResponse({"ok": False, "error": f"unknown system {key!r}"}, status=400)

    sql = (request.POST.get("sql") or "").strip()
    limit = min(max(int(request.POST.get("limit") or 200), 1), QUERY_ROW_CAP)

    reason = _reject_reason(sql)
    if reason:
        return JsonResponse({"ok": False, "error": reason, "refused": True}, status=400)

    started = time.perf_counter()
    try:
        # autocommit off, and the transaction is rolled back either way -- so even
        # if something slipped past the guard it does not survive the call.
        with connections.open(key, autocommit=False, timeout=QUERY_TIMEOUT_S) as conn:
            conn.timeout = QUERY_TIMEOUT_S
            cur = conn.cursor()
            try:
                cur.execute(sql)
                if cur.description is None:
                    return JsonResponse({"ok": False,
                                         "error": "that statement returned no result set"},
                                        status=400)
                columns = [c[0] for c in cur.description]
                rows = [[_json_safe(v) for v in r] for r in cur.fetchmany(limit + 1)]
            finally:
                conn.rollback()
    except Exception as exc:
        return JsonResponse({"ok": False, "elapsed_ms": int((time.perf_counter() - started) * 1000),
                             "error": f"{type(exc).__name__}: {exc}"}, status=200)

    truncated = len(rows) > limit
    return JsonResponse({"ok": True, "columns": columns, "rows": rows[:limit],
                         "row_count": min(len(rows), limit), "truncated": truncated,
                         "limit": limit, "target": profile(key).describe(),
                         "elapsed_ms": int((time.perf_counter() - started) * 1000)})
