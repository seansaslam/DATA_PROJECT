"""Hand-over notes: how to join the three systems, and how to ingest them.

This is the part of a data dictionary that usually only exists in someone's head.
The column list tells you what exists; this tells you what will bite you.
"""
from __future__ import annotations

JOIN_RECIPES = [
    {
        "title": "Daily actual vs forecast, one row per operated well per day",
        "why": ("The delivery query the whole estate exists to support. Actual sales "
                "volumes from ProCount, the budget case from ARIES, well header from "
                "the well master. Written here as it would look once all three schemas "
                "have landed in one Snowflake database."),
        "sql": """-- Grain: one row per API_UWI per day.
-- Built from the union of both date spines, because a well can have an actual
-- with no forecast (a well drilled after the budget was locked) or a forecast
-- with no actual (a PUD that has not come online yet).
WITH spine AS (
    SELECT c.API_UWI, d.PROD_DATE AS THE_DATE
      FROM PROCOUNT.PC.WELL_DAILY_PROD d
      JOIN PROCOUNT.PC.PC_COMPLETION  c ON c.MERRICK_ID = d.MERRICK_ID
    UNION
    SELECT p.API_UWI, f.D_DATE
      FROM ARIES.AC.AC_DAILY    f
      JOIN ARIES.AC.AC_PROPERTY p ON p.PROPNUM = f.PROPNUM
)
SELECT
    s.API_UWI,
    s.THE_DATE,
    w.WELL_NAME,
    w.AREA,
    w.PAD_NAME,
    w.CENTRAL_FACILITY,
    w.WELL_STATUS,
    w.NET_REVENUE_INTEREST,
    d.OIL_BBL                       AS ACT_OIL_BBL,
    d.GAS_MCF                       AS ACT_GAS_MCF,
    d.NGL_BBL                       AS ACT_NGL_BBL,
    d.ALLOCATION_STATUS,
    f.GROSS_OIL_BBL                 AS FCST_OIL_BBL,
    f.GROSS_GAS_MCF                 AS FCST_GAS_MCF,
    f.GROSS_NGL_BBL                 AS FCST_NGL_BBL,
    COALESCE(d.OIL_BBL, 0) - COALESCE(f.GROSS_OIL_BBL, 0) AS VAR_OIL_BBL,
    COALESCE(d.OIL_BBL, 0) + COALESCE(d.NGL_BBL, 0)
        + COALESCE(d.GAS_MCF, 0) / 6.0                    AS ACT_BOE
FROM spine s
LEFT JOIN WELL_MASTER.WM.CCT_WELL_MASTER w
       ON w.API_UWI = s.API_UWI
LEFT JOIN PROCOUNT.PC.PC_COMPLETION c
       ON c.API_UWI = s.API_UWI
LEFT JOIN PROCOUNT.PC.WELL_DAILY_PROD d
       ON d.MERRICK_ID = c.MERRICK_ID AND d.PROD_DATE = s.THE_DATE
LEFT JOIN ARIES.AC.AC_PROPERTY p
       ON p.API_UWI = s.API_UWI
LEFT JOIN ARIES.AC.AC_DAILY f
       ON f.PROPNUM = p.PROPNUM AND f.D_DATE = s.THE_DATE;""",
        "watch": [
            "Join through pc_completion, not on well_daily_prod.API_UWI. The daily "
            "table carries the API as a convenience copy; pc_completion is where the "
            "MERRICK_ID to API mapping is actually maintained, and it is the copy that "
            "gets corrected when a well is re-keyed.",
            "Every join is LEFT. An inner join drops the 1,400 non-operated wells, "
            "then drops any operated well missing from either vendor system, and the "
            "row count looks plausible the whole way down.",
            "Do not sum NET_* and gross volumes in the same measure. NET_ is already "
            "multiplied by the net revenue interest.",
            "6 Mcf to 1 BOE is a volumetric convention, not an energy or value one. "
            "Finance may use a different ratio; agree it before publishing.",
        ],
    },
    {
        "title": "Reconciling well counts between the three systems",
        "why": ("The first query to run after any load, and the one that catches a "
                "broken ingestion faster than any row count. If these numbers move, "
                "something upstream changed."),
        "sql": """SELECT
    COUNT(*)                                                   AS well_master_rows,
    SUM(CASE WHEN w.OPERATED_FLAG = 1 THEN 1 ELSE 0 END)       AS operated,
    SUM(CASE WHEN c.MERRICK_ID IS NULL AND w.OPERATED_FLAG = 1
             THEN 1 ELSE 0 END)                                AS operated_missing_procount,
    SUM(CASE WHEN p.PROPNUM IS NULL AND w.OPERATED_FLAG = 1
             THEN 1 ELSE 0 END)                                AS operated_missing_aries,
    SUM(CASE WHEN c.MERRICK_ID IS NOT NULL AND w.OPERATED_FLAG = 0
             THEN 1 ELSE 0 END)                                AS non_operated_in_procount
FROM WELL_MASTER.WM.CCT_WELL_MASTER w
LEFT JOIN PROCOUNT.PC.PC_COMPLETION c ON c.API_UWI = w.API_UWI
LEFT JOIN ARIES.AC.AC_PROPERTY      p ON p.API_UWI = w.API_UWI;""",
        "watch": [
            "operated_missing_procount should be 0 in steady state, but will be "
            "non-zero for a day or two after a new well comes online -- the systems "
            "are updated by different teams on different cycles.",
            "non_operated_in_procount should always be 0. Anything else means a "
            "non-operated well has been set up for production accounting by mistake.",
            "An API that appears in ProCount or ARIES but not in the well master is "
            "the worst case, because a left join from the well master will never "
            "show it. Check that direction separately.",
        ],
    },
    {
        "title": "Downtime without fanning out the daily table",
        "why": ("pc_daily_downtime is one row per reason, so a well that lost time "
                "twice in a day has two rows. Joining it straight onto the daily "
                "production table doubles that day's volumes."),
        "sql": """WITH dt AS (
    SELECT MERRICK_ID,
           DOWNTIME_DATE,
           SUM(DOWNTIME_HOURS)   AS DOWNTIME_HOURS,
           SUM(DEFERRED_OIL_BBL) AS DEFERRED_OIL_BBL,
           COUNT(*)              AS EVENT_COUNT,
           MIN(REASON_CODE)      AS FIRST_REASON
      FROM PROCOUNT.PC.PC_DAILY_DOWNTIME
     GROUP BY MERRICK_ID, DOWNTIME_DATE
)
SELECT d.MERRICK_ID, d.PROD_DATE, d.OIL_BBL,
       COALESCE(dt.DOWNTIME_HOURS, 0) AS DOWNTIME_HOURS,
       dt.EVENT_COUNT, dt.FIRST_REASON
  FROM PROCOUNT.PC.WELL_DAILY_PROD d
  LEFT JOIN dt ON dt.MERRICK_ID = d.MERRICK_ID
              AND dt.DOWNTIME_DATE = d.PROD_DATE;""",
        "watch": [
            "well_daily_prod.DOWNTIME_HOURS is the allocated total and is the column "
            "to trust for uptime maths. The detail table explains it; it does not "
            "always sum to exactly the same number.",
            "No downtime row means no lost time, not unknown. COALESCE to 0.",
        ],
    },
    {
        "title": "Month-to-date volumes that will not move under you",
        "why": ("ALLOCATION_STATUS is the difference between a number you can report "
                "and a number that will be restated next week."),
        "sql": """SELECT
    DATE_TRUNC('month', PROD_DATE) AS PROD_MONTH,
    ALLOCATION_STATUS,
    COUNT(DISTINCT MERRICK_ID)     AS WELLS,
    SUM(OIL_BBL)                   AS OIL_BBL,
    SUM(GAS_MCF)                   AS GAS_MCF
  FROM PROCOUNT.PC.WELL_DAILY_PROD
 WHERE PROD_DATE >= DATEADD('month', -3, CURRENT_DATE)
 GROUP BY 1, 2
 ORDER BY 1 DESC, 2;""",
        "watch": [
            "ESTIMATED covers roughly the last 3 days and can move by 10% or more.",
            "ALLOCATED covers to about 35 days and typically moves by a few percent "
            "as gas plant statements and run tickets arrive.",
            "FINAL is closed and only changes through a deliberate prior-period "
            "adjustment -- which does happen, and is why even FINAL rows have an "
            "UPDATED_TS worth watching.",
        ],
    },
]


INGESTION_NOTES = [
    {
        "heading": "What to key each table on",
        "body": [
            "wm.cct_well_master -- API_UWI. Stable; a well is never re-keyed in "
            "practice, though its attributes change constantly.",
            "pc.pc_completion -- MERRICK_ID. The API on this table can and does get "
            "corrected, so do not key on it.",
            "pc.well_daily_prod -- MERRICK_ID + PROD_DATE.",
            "pc.pc_daily_downtime -- MERRICK_ID + DOWNTIME_DATE + SEQ_NO.",
            "ac.AC_PROPERTY -- PROPNUM.",
            "ac.AC_DAILY -- PROPNUM + D_DATE.",
        ],
    },
    {
        "heading": "Volume and change profile, per load",
        "body": [
            "wm.cct_well_master -- about 2,000 rows total, a handful change a day. "
            "Full reload every run is entirely reasonable and removes a whole class "
            "of problem. Do not build CDC for this table just because you can.",
            "pc.well_daily_prod -- the big one. About 135,000 rows today and growing "
            "by roughly 600 a day. The catch is that it is not append-only: the last "
            "35 days of rows are restated as allocation firms up, so 'load yesterday' "
            "is wrong and 'load the last 45 days' is the usual pragmatic answer.",
            "ac.AC_DAILY -- about 220,000 rows, and a single re-forecast rewrites "
            "every future row for that property at once. A watermarked load looks "
            "quiet for days and then moves 60,000 rows in one run; size the pipeline "
            "for the spike, not the average.",
            "pc.pc_daily_downtime -- small and sparse, but rows do get deleted when a "
            "pumper corrects a mis-coded event.",
        ],
    },
    {
        "heading": "Choosing a strategy",
        "body": [
            "FULL -- correct by construction, needs nothing enabled on the source. "
            "Right answer for both master tables and for anything under a few hundred "
            "thousand rows that you can afford to re-read.",
            "INCREMENTAL on UPDATED_TS -- cheapest, and the one that quietly drifts. "
            "It cannot see a hard delete, and it misses rows whose UPDATED_TS was not "
            "maintained. Only safe where deletes do not happen or do not matter, and "
            "then only with a periodic full reconcile.",
            "Change Tracking -- tells you which rows changed and that a row was "
            "deleted, but not what it used to hold, so the loader joins back to the "
            "base table. Light on the source. Needs a primary key, which every table "
            "here has. Retention is a real limit: if the pipeline is down longer than "
            "CHANGE_RETENTION, the watermark expires and you must re-baseline.",
            "CDC -- the capture table carries the column values themselves, including "
            "the before-image of a delete, so it is the only option when you need to "
            "know what a deleted row contained. Heavier: it reads the transaction log "
            "through an Agent job on-prem, and the capture tables need their own "
            "cleanup and monitoring.",
        ],
    },
    {
        "heading": "Enabling the mechanism a tool will use",
        "body": [
            "Fivetran on SQL Server uses CT or CDC depending on how the connector is "
            "configured; both need enabling per database and per table before the "
            "connector will do anything but a full re-sync.",
            "Azure Data Factory has no change mechanism of its own -- a 'delta' "
            "pipeline is you writing a watermarked query, or you querying CHANGETABLE "
            "or the cdc functions directly. The watermark lives in ADF, not in the "
            "source.",
            "Change Tracking needs ALTER DATABASE, which db_owner has. Enabling CDC on "
            "a full SQL Server instance needs sysadmin for the one-time "
            "sys.sp_cdc_enable_db, plus a running SQL Server Agent -- the capture job "
            "is what actually fills the change tables. Azure SQL Database needs "
            "neither; db_owner can enable CDC and the capture runs as a managed process.",
            "Turning tracking off silently invalidates any stored watermark. Changes "
            "made while a table was untracked are gone, and SQL Server cannot warn "
            "you, because CHANGE_TRACKING_MIN_VALID_VERSION only advances when the "
            "database version advances. Always delete the watermark when you disable "
            "a mechanism, so the next run re-baselines.",
        ],
    },
    {
        "heading": "Landing these in Snowflake",
        "body": [
            "Keep the source primary keys as the natural keys. Do not invent a "
            "surrogate for MERRICK_ID or PROPNUM -- they already are surrogates, from "
            "the systems that own them.",
            "Land raw first, one schema per source system, names unchanged. The "
            "temptation to conform WELL_NAME across ProCount, ARIES and the well "
            "master during ingestion is the temptation to lose evidence of a real "
            "data quality problem.",
            "decimal(12,2) maps to NUMBER(12,2); datetime2(3) to TIMESTAMP_NTZ(3). The "
            "UPDATED_TS columns are UTC (SYSUTCDATETIME), and the date columns are "
            "wall-clock production dates with no timezone -- do not shift them.",
            "Deletes: if you use CT or CDC, land them as a soft-delete flag rather "
            "than removing the row. A production report that changes retrospectively "
            "with no trace is worse than one that is wrong.",
        ],
    },
    {
        "heading": "Data quality checks worth automating",
        "body": [
            "Every API_UWI is 14 characters, starts with 42, and characters 3-5 match "
            "the county code for the COUNTY value.",
            "PRODUCING_HOURS + DOWNTIME_HOURS = 24 on every daily row.",
            "No production row earlier than the well's FIRST_PROD_DATE.",
            "NET_OIL_BBL <= OIL_BBL on every row, and the ratio equals the well's "
            "NET_REVENUE_INTEREST to within rounding.",
            "Non-operated wells (OPERATED_FLAG = 0) appear in neither ProCount nor "
            "ARIES.",
            "Row count per day in well_daily_prod is stable: a sudden drop means the "
            "daily allocation job did not finish, not that production stopped.",
        ],
    },
]
