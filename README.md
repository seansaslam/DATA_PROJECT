# CCT Oil & Cattle — multi-system source simulator

Three realistic, independent oil-and-gas source databases on SQL Server, a simulator that
keeps them moving, and the documentation to hand them to a data engineer.

The exercise is the **source side**. There is no warehouse — ADF or Fivetran will move
these tables to Snowflake. What this gives you is sources that behave like real ones, the
change mechanisms an ingestion tool needs, and a way to see what each ingestion strategy
would actually move.

## Quick start

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

.\.venv\Scripts\python.exe seed_cli.py --drop   # build and load, ~15 seconds
.\.venv\Scripts\python.exe run_app.py           # http://localhost:5001/
```

Or `.\start.ps1` / `.\stop.ps1` to run it in the background.

Port **5001** — 5000 is the JDE ERP simulator in `../jde_erp`. Set `CCT_APP_PORT` to move
it.

## What is in the databases

CCT Oil & Cattle, a Texas operator producing since 1 January 2026: 603 operated wells and
1,400 non-operated, across the Permian and Eagle Ford.

| Database | Schema | Tables | Keyed on |
|---|---|---|---|
| `WELL_MASTER_DB` | `wm` | `cct_well_master` | `API_UWI` |
| `PROCOUNT_DB` | `pc` | `pc_completion`, `well_daily_prod`, `pc_daily_downtime` | `MERRICK_ID` |
| `ARIES_DB` | `ac` | `AC_PROPERTY`, `AC_DAILY` | `PROPNUM` |

About 358,000 rows in total. No foreign keys anywhere: three separate vendor products,
joined on primary key values. `API_UWI` is the only value common to all three, and it is
the primary key of exactly one of them — which is the problem the exercise is about.

## The pages

- **Dashboard** — production headline, per-system status, the simulator, live mode
- **Well Master / ProCount / ARIES** — one page per application: connection, tables, row
  counts, tracking state, and a searchable record browser
- **Change feed** — arm FULL / INCREMENTAL / CT / CDC per system and compare what each
  would actually move
- **Data dictionary & ERD** — built from the live catalog; prints to PDF for hand-over
- **Connections** — per-database credentials and type (SQL Server, Azure SQL, Azure SQL MI)

## The exercise

1. Rebuild the databases.
2. Arm ProCount for `INCREMENTAL`, read once to baseline.
3. **Simulate operations** on the dashboard. It adds a production day, restates
   allocations, shuts a well in, brings a new well online — and deletes a few rows.
4. Read again: the updates come through, the deletes do not. They never will.
5. Arm it for `CT`, read to baseline, simulate again, read again. Now the deletes appear.
6. Compare `rows read` per strategy in the read history.

`verify.py` asserts that whole sequence, so a change to the extractors cannot quietly
break the lesson.

## CDC

Change Tracking works out of the box. CDC needs one sysadmin command per database, plus
SQL Server Agent running:

```sql
USE [PROCOUNT_DB];
EXEC sys.sp_cdc_enable_db;
```

Then `python verify.py --cdc`. Azure SQL Database needs neither — `db_owner` can enable
CDC itself.

## Configuration

Connections are edited on the **Connections** page and stored in `app_state.sqlite3`, not
in source. First-run defaults come from `.env` (see `.env.example`).

See `CLAUDE.md` for the design, conventions and the correctness details worth keeping.
