# CCT Oil & Cattle — multi-system source simulator

A Django app that stands up and keeps alive three realistic, independent oil-and-gas
source databases on SQL Server, so a data engineer can practise ingesting them.

The point of the exercise is the **source side**. There is deliberately no warehouse
here: ADF or Fivetran will move these tables to Snowflake later. What this app owns is
making the sources real — realistic wells and volumes, a simulator that keeps them
moving, the change mechanisms an ingestion tool needs (full, incremental, Change
Tracking, CDC), and documentation good enough to hand over.

```
python run_app.py          # http://localhost:5001/   (5000 is the JDE simulator)
python seed_cli.py --drop  # rebuild and reload all three databases from the CLI
python verify.py           # end-to-end correctness harness
python verify.py --cdc     # ... including CDC, once a sysadmin has enabled it
```

---

## The company

CCT Oil & Cattle, a Texas operator producing since **1 January 2026**.

| | |
|---|---|
| Operated wells | 603 — these reach ProCount and ARIES |
| Non-operated wells | 1,400 — working interest only, well master only |
| Areas | Midland Basin North / Core / South, Northern Shelf, Eagle Ford East / West |
| Counties | 15 real Texas counties, with their real API county codes |
| Seeded volume | ~358,000 rows, loaded in under 15 seconds |

Wells decline on a hyperbolic Arps curve driven by lateral length; about 6% shut in
during the year and produce explicit zeros; days lose time to real deferment reasons.
Everything is generated from one seed (`systems/generator.py: SEED`), so a rebuild
reproduces the same company.

## The three databases

All on one SQL Server, all reached by the same service account, each with its own
connection profile. Host and credentials come from `.env` (see `.env.example`).
**No foreign keys anywhere** — these are three separate vendor products, linked by
primary key *value* only, which is exactly the situation being simulated.

| System | Database | Schema | Tables | Keyed on |
|---|---|---|---|---|
| Well Master | `WELL_MASTER_DB` | `wm` | `cct_well_master` | `API_UWI` |
| ProCount | `PROCOUNT_DB` | `pc` | `pc_completion`, `well_daily_prod`, `pc_daily_downtime` | `MERRICK_ID` |
| ARIES | `ARIES_DB` | `ac` | `AC_PROPERTY`, `AC_DAILY` | `PROPNUM` |

The central teaching point is in that last column. `API_UWI` is the only value common to
all three systems, and it is the primary key of exactly one of them. ProCount keys on
`MERRICK_ID`, its own completion surrogate; ARIES keys on `PROPNUM`, its 10-character
property id. Both carry the API as an ordinary attribute maintained by their own team,
and they are allowed to disagree with the well master on `WELL_NAME`, `AREA` and
`WELL_STATUS` — because in real life they do.

Every table carries `CREATED_TS` and `UPDATED_TS` (UTC, `SYSUTCDATETIME()`).
`UPDATED_TS` is the audit column a watermarked load keys off.

### Row counts after a clean seed

```
wm.cct_well_master        2,003     603 operated + 1,400 non-operated
pc.pc_completion            603     operated only
pc.well_daily_prod      ~128,000    first production -> today, ~600/day thereafter
pc.pc_daily_downtime      ~6,700    sparse: only days that lost time
ac.AC_PROPERTY              603     operated only
ac.AC_DAILY             220,095    603 wells x 365 days of calendar 2026
```

## The app

| Page | What it does |
|---|---|
| **Dashboard** | Headline production numbers, per-system connection and tracking state, the simulator controls, live mode |
| **Well Master / ProCount / ARIES** | Per-application page: its connection, its tables, row counts, change-mechanism state, and a paged searchable record browser |
| **Change feed** | Arm each system for FULL / INCREMENTAL / CT / CDC, read what a load would pull, compare rows-read across strategies |
| **Transformations** | A SQL curriculum over these three sources, basics to intermediate: title, description, SQL Server syntax, Snowflake syntax. Copy either; run the T-SQL in the built-in read-only scratchpad |
| **Data dictionary & ERD** | Live catalog, per-column descriptions, ERD, join recipes, ingestion notes. Prints to PDF |
| **Connections** | Per-database credentials and type. Save and test |

### Connections page

Each application database is configured independently — type, server, port, database,
username, password, extra ODBC options — stored in the app's own SQLite file
(`app_state.sqlite3`), not in source. Supported types are SQL Server, Azure SQL Database
and Azure SQL Managed Instance; the capability matrix on that page shows what each one
supports. Nothing else in the codebase branches on connection type, so ProCount can move
to Azure SQL while the Well Master stays on-prem and every page, strategy and document
keeps working. Adding a type means adding one `Dialect` to `core/dialects.py`.

Leaving a password field blank keeps the stored one.

### Simulator — random activity

`systems/churn.py`. One tick is one operational cycle:

| Activity | What it writes |
|---|---|
| New production day | ~600 new `well_daily_prod` rows plus downtime events |
| Allocation revisions | Updates prior days, promoting ESTIMATED → ALLOCATED → FINAL and restating volumes |
| Well master edits | Shut-ins, returns to production, interest corrections, facility changes — and ProCount follows the shut-in |
| Forecast revisions | Re-runs a type curve, rewriting every future `AC_DAILY` row for that property at once |
| New wells online | A new well appears in all three systems in the same tick |
| Hard deletes | Removes rows outright |

Scale multiplies everything except the production day, which is always exactly one day.
**Live mode** on the dashboard fires a tick every N seconds so the sources keep moving on
their own; it can read the change feed after each tick too.

Hard deletes matter most. They are the only change a watermarked incremental load cannot
see, and they are why CT and CDC exist.

### The four strategies

Each system is armed independently, so you can run the Well Master on FULL while ProCount
runs on CDC and compare them in the same log. A read **extracts and counts**; it does not
write the rows anywhere.

| | Reads | Sees deletes | Needs on the source | Watermark |
|---|---|---|---|---|
| `FULL` | everything, every time | n/a — it reloads | nothing | none |
| `INCREMENTAL` | `WHERE UPDATED_TS > watermark` | **no** | an audit column | timestamp |
| `CT` | `CHANGETABLE(CHANGES …)` + join back for values | yes, keys only | `ALTER DATABASE … SET CHANGE_TRACKING` | version number |
| `CDC` | `cdc.fn_cdc_get_net_changes_*` — values included | yes, with the row's contents | `sp_cdc_enable_db` + Agent | LSN |

Both incremental mechanisms **bootstrap with a full baseline** on their first run, and
**re-baseline automatically** when their retained history no longer covers the stored
watermark.

Watermarks and run history live in `app_state.sqlite3`, never in the source databases —
the same place ADF keeps a pipeline variable and Fivetran keeps its cursor. A data
engineer pointing an ingestion tool at `WELL_MASTER_DB` should see the well master and
nothing else.

Switching strategy clears that system's watermarks so the next read baselines and the
numbers stay comparable between modes. The change feed page also has an opt-in
**drop & recreate source tables** switch for a genuinely clean slate; it is off by default
because it destroys the seeded production history.

## CDC needs one sysadmin command

The service account is `db_owner`, which is enough for all of Change Tracking and for managing
CDC *capture instances* — but `sys.sp_cdc_enable_db` requires **sysadmin**. Run once per
database:

```sql
USE [PROCOUNT_DB];
EXEC sys.sp_cdc_enable_db;
```

CDC also needs **SQL Server Agent running** — the capture job is what actually populates
the change tables. The app checks `cdc.lsn_time_mapping` and warns if capture looks idle.
Until then FULL, INCREMENTAL and CT all work normally.

Azure SQL Database needs neither: `db_owner` can enable CDC itself and the capture runs as
a managed process. That difference is encoded in `Dialect.cdc_needs_sysadmin`.

## Two correctness details worth keeping

**A failed mode switch must not disarm the live mechanism.** `switch_mode` arms the
incoming mechanism *before* tearing the outgoing one down. Disarm first, and a CDC switch
that fails on the sysadmin check leaves the database with no tracking at all — and the
next "incremental" read reports zero changes while the source quietly drifts.

**Disabling tracking invalidates the watermark.** Changes made while a table's tracking
was off are gone for good, and SQL Server cannot warn you:
`CHANGE_TRACKING_MIN_VALID_VERSION` only advances when the *database* version advances, so
with every table untracked a stale watermark still looks valid. So disabling a mechanism
also deletes its watermark, forcing the next read to re-baseline. `verify.py` asserts both.

### Transformations page

`documentation/transformations.py` holds 48 lessons in 11 sections, from `SELECT` through
type conformance, window functions and load patterns. Each is Title -> Description -> **SQL Server syntax**
-> **Snowflake syntax**, with a "watch out" list for the places the two dialects or the
data disagree.

Table names in the lesson text are tokens (`{PC_DAILY}`, `{AC_PROP}` ...) expanded per
dialect at render time, so one text produces both blocks and the T-SQL comes out
three-part qualified against whatever the Connections page currently points at. That is
what makes **Run** work: the query executes exactly as displayed, and a cross-database
join reads `PROCOUNT_DB.pc.well_daily_prod` from the ARIES connection just as it would
from a client.

The **Types, dates and time zones** section is the one that needs live data to teach:
`cast-to-join` returns `MATCHED_WITHOUT_CAST = 0` next to `MATCHED_WITH_CAST = 559` on
one row, because `date` never equals `datetime2(3)`. `AT TIME ZONE` returns
`datetimeoffset`, which pyodbc cannot decode -- `sqlutil.connect` registers an output
converter for SQL type -155 so a UTC-to-Central query renders instead of failing.

The scratchpad (`api_query`) is the one place in the app that executes SQL a user typed,
and it is guarded twice. `_reject_reason` scrubs string literals and comments, then
requires a single statement starting `SELECT` or `WITH` with no write keyword; the
statement then runs inside a transaction that is rolled back either way. Lessons that
write -- `MERGE`, `CREATE VIEW`, `INSERT ... SELECT` -- are marked `runnable=False` and
shown without a Run button.

`verify.py` executes every runnable lesson against the live source and fails if one no
longer runs, so a column rename cannot leave broken SQL on the page. It also asserts the
guard agrees with the `runnable` flag in both directions.

## Documentation

`/docs/` builds itself from the **live catalog** on every load, so it cannot drift from
the deployed schema. It carries, per table: grain, primary key, row count, and every
column with type, nullability, default and a business description. Then the ERD, the
relationship notes, four annotated join recipes written as they would look in Snowflake,
and the ingestion notes. Print to PDF from that page for hand-over.

The one thing a catalog cannot supply is meaning, so descriptions live in
`documentation/descriptions.py` and join/ingestion guidance in `documentation/joins.py`.
A column added to the DDL without a description is reported by `verify.py` rather than
silently shipping a blank cell.

## Layout

```
db/10_wellmaster.sql          wm.cct_well_master
db/20_procount.sql            pc.pc_completion / well_daily_prod / pc_daily_downtime
db/30_aries.sql               ac.AC_PROPERTY / AC_DAILY

core/registry.py              the one place that knows what the databases contain
core/dialects.py              database-type abstraction (SQL Server, Azure SQL, MI)
core/models.py                connection profiles, watermarks, run log  (SQLite)
core/connections.py           profile -> live pyodbc connection
core/sqlutil.py               pyodbc helpers, GO batch splitting, fast_executemany
core/jobs.py                  background jobs + the live-mode ticker
core/views.py, urls.py        pages and JSON endpoints
core/templates/core/          Bootstrap 5 UI

systems/generator.py          well population, Arps decline, Texas geography
systems/seeder.py             create schemas, bulk load
systems/churn.py              random activity: the company in motion

etl/tracking.py               CT / CDC arm, disarm, health
etl/extract.py                the four strategies, side by side
etl/watermark.py              watermark storage (app-side, not in the sources)
etl/runner.py                 orchestration, mode switching, run history

documentation/catalog.py      live catalog -> data dictionary + ERD SVG
documentation/descriptions.py business meaning per column
documentation/joins.py        join recipes and ingestion notes
documentation/transformations.py  the SQL curriculum, both dialects

run_app.py                    start the app
seed_cli.py                   build and load from the command line
verify.py                     end-to-end correctness harness
```

## Conventions

- **Raw pyodbc for the source databases, Django ORM only for app state.** The source DDL
  is vendor-shaped, has no foreign keys, and must stay byte-identical to the SQL a data
  engineer would be handed. Modelling it in the ORM would fight that.
- **`core/registry.py` is the single source of truth.** Add a column to the DDL and to
  `TableSpec.columns`, and the seeder, the extractors, the record browser and the docs all
  pick it up. Nothing else hard-codes a column list.
- **Identifiers never come from user input.** Everything built into SQL goes through
  `sqlutil.ident()`, which rejects anything that is not a plain identifier. Values are
  always parameters.
- **`fast_executemany`** on every bulk insert — the difference between a 15-second seed and
  a 20-minute one.
- **Timestamps are naive UTC** and `USE_TZ = False`, because that is what the sources
  produce. Making Django attach a timezone would promote a `datetime2` comparison to
  `datetimeoffset` behind your back.
- The app runs on **5001**. Port 5000 is the JDE ERP simulator in
  `../jde_erp`, whose structure this project follows. Override with `CCT_APP_PORT`.
