"""Business meaning for every column.

The catalog can tell you a column is decimal(12,2). It cannot tell you that
NET_OIL_BBL is already multiplied by the net revenue interest and must never be
summed alongside a working-interest volume. That is what lives here.

Anything in the deployed schema without an entry is listed as undocumented at
the end of a docs build rather than silently shipping a blank cell.
"""
from __future__ import annotations

# Columns that mean the same thing wherever they appear.
COMMON = {
    "API_UWI": ("14-digit API / unique well identifier. 42 = Texas, then the 3-digit "
                "county code, a 5-digit unique number, and a 4-digit sidetrack / "
                "completion suffix. The only value that links all three systems."),
    "CREATED_TS": "UTC timestamp of the row's first insert. Set by the source system.",
    "UPDATED_TS": ("UTC timestamp of the last change. This is the audit column a "
                   "watermarked incremental load keys off -- and the reason such a "
                   "load can never see a hard delete."),
    "WELL_NAME": "Lease / unit name as carried by the system that owns the row.",
    "AREA": ("CCT operating area. Six areas across the Permian and Eagle Ford: "
             "Midland Basin North / Core / South, Northern Shelf, Eagle Ford East / West."),
    "PAD_NAME": "Surface pad the well was drilled from. Several wells share a pad.",
    "COUNTY": "Texas county. Must agree with the county code inside API_UWI.",
    "STATE_CODE": "US state postal code. Always TX in this build.",
    "OPERATOR_NAME": "Operator of record. CCT OIL & CATTLE where OPERATED_FLAG = 1.",
    "FIELD_NAME": "Railroad Commission field name.",
    "RESERVOIR": "Producing interval, e.g. WOLFCAMP A, SPRABERRY LOWER, EAGLE FORD LOWER.",
    "WELL_STATUS": "PRODUCING, SHUT-IN, TA (temporarily abandoned) or DUC.",
    "FIRST_PROD_DATE": "First date the well produced to sales. Nothing exists before it.",
    "WORKING_INTEREST": "CCT's share of costs, 0-1. Gross volumes are not adjusted by it.",
    "NET_REVENUE_INTEREST": ("CCT's share of revenue, 0-1, after royalty burdens. Every "
                             "NET_* volume is the gross volume multiplied by this."),
    "SURFACE_LATITUDE": "Surface hole latitude, WGS84 decimal degrees.",
    "SURFACE_LONGITUDE": "Surface hole longitude, WGS84 decimal degrees.",
    "MAJOR_PHASE": "OIL or GAS. Drives which decline curve the forecast is built on.",
    "SCENARIO": "Named forecast case, e.g. 2026 BUDGET.",
    "RESERVE_CAT": "SPE reserve category: PDP, PDNP or PUD.",
    "MERRICK_ID": ("ProCount's own integer surrogate for a completion. ProCount keys "
                   "everything on this, not on the API; resolving MERRICK_ID to an "
                   "API is only possible through pc.pc_completion."),
    "PROPNUM": ("ARIES property id, 10 hex characters. ARIES keys everything on this, "
                "not on the API."),
}

TABLES: dict[str, dict[str, str]] = {

    # ---------------------------------------------------------------- WELL MASTER
    "wm.cct_well_master": {
        "WELL_NUMBER": "Well number within the lease, e.g. 3AH. Horizontal wells end in H.",
        "OPERATED_FLAG": ("1 = CCT operates the well (603 of them). 0 = CCT holds a "
                          "working interest but someone else operates (1,400). Only "
                          "operated wells reach ProCount and ARIES."),
        "CENTRAL_FACILITY": ("Central tank battery the well flows to. Allocation happens "
                             "at this level, which is why two wells on one battery can "
                             "never be measured perfectly independently."),
        "BASIN": "PERMIAN or EAGLE FORD.",
        "WELL_TYPE": "OIL or GAS, by major phase at first production.",
        "BOTTOMHOLE_LATITUDE": "Bottom hole latitude. Differs from surface on a horizontal.",
        "BOTTOMHOLE_LONGITUDE": "Bottom hole longitude.",
        "SPUD_DATE": "Date the drilling bit first turned to the right.",
        "COMPLETION_DATE": "Date the completion (frac) finished.",
        "LATERAL_LENGTH_FT": "Horizontal lateral length in feet. The main driver of initial rate.",
        "TOTAL_DEPTH_FT": "True vertical depth in feet.",
    },

    # ------------------------------------------------------------------- PROCOUNT
    "pc.pc_completion": {
        "COMPLETION_NAME": "ProCount's own name for the completion; not guaranteed to "
                           "match WELL_NAME in the well master, which is the point.",
        "PROD_ROUTE": "Pumper route the well is gauged on.",
        "BATTERY_ID": "Tank battery identifier volumes are allocated from.",
        "BATTERY_NAME": "Tank battery name.",
        "ACTIVE_FLAG": "0 once the completion is shut in; the daily job stops writing rows.",
        "ALLOC_METHOD": ("How the battery total is split between wells: TEST (well test "
                         "ratios), METER (individual meters) or THEORETICAL."),
    },
    "pc.well_daily_prod": {
        "PROD_DATE": "Production day. Grain is one row per MERRICK_ID per day.",
        "OIL_BBL": "Gross allocated oil, barrels. 100% of the wellbore, before interests.",
        "GAS_MCF": "Gross allocated gas, thousand cubic feet.",
        "WATER_BBL": "Gross produced water, barrels. Not a sales volume.",
        "NGL_BBL": "Gross natural gas liquids, barrels, recovered downstream of the plant.",
        "NET_OIL_BBL": "OIL_BBL x net revenue interest. CCT's revenue volume.",
        "NET_GAS_MCF": "GAS_MCF x net revenue interest.",
        "NET_NGL_BBL": "NGL_BBL x net revenue interest.",
        "PRODUCING_HOURS": "Hours on production, 0-24. 24 minus DOWNTIME_HOURS.",
        "DOWNTIME_HOURS": "Hours lost. Detail sits in pc_daily_downtime.",
        "TUBING_PRESSURE_PSI": "Flowing tubing pressure at the wellhead.",
        "CASING_PRESSURE_PSI": "Casing pressure at the wellhead.",
        "CHOKE_SIZE_64THS": "Surface choke opening in 64ths of an inch.",
        "WELL_STATUS_CODE": "PRODUCING, SHUT-IN or DOWN on that day.",
        "ALLOCATION_STATUS": ("ESTIMATED for the last 3 days, ALLOCATED to about 35 days, "
                              "FINAL after close. Volumes are restated as this advances, "
                              "which is the main source of legitimate late-arriving updates."),
    },
    "pc.pc_daily_downtime": {
        "DOWNTIME_DATE": "Day the time was lost.",
        "SEQ_NO": "Sequence within the day; a well can lose time for more than one reason.",
        "DOWNTIME_HOURS": "Hours lost to this reason.",
        "REASON_CODE": "Short deferment code, e.g. ART, COMP, ELEC, FL, WO, WX.",
        "REASON_DESC": "Readable description of the deferment reason.",
        "DEFERRED_OIL_BBL": "Oil not produced because of this event, versus the day's potential.",
        "DEFERRED_GAS_MCF": "Gas not produced because of this event.",
    },

    # ---------------------------------------------------------------------- ARIES
    "ac.AC_PROPERTY": {
        "LEASE_NAME": "Lease the property rolls up to for economics.",
        "TYPE_CURVE": "Named type curve the forecast was built from.",
        "EFFECTIVE_DATE": "Date the current forecast case takes effect.",
        "QI_OIL_BOPD": "Arps initial oil rate, barrels per day, at FIRST_PROD_DATE.",
        "DI_NOMINAL": "Arps initial nominal decline, annual fraction.",
        "B_FACTOR": ("Arps hyperbolic exponent. 0 is exponential, 1 is harmonic; "
                     "unconventional wells sit near 1.0-1.4."),
    },
    "ac.AC_DAILY": {
        "D_DATE": "Forecast day. Grain is one row per PROPNUM per day for calendar 2026.",
        "GROSS_OIL_BBL": "Forecast gross oil, barrels per day.",
        "GROSS_GAS_MCF": "Forecast gross gas, Mcf per day.",
        "GROSS_WATER_BBL": "Forecast gross water, barrels per day.",
        "GROSS_NGL_BBL": "Forecast gross NGL, barrels per day.",
        "NET_OIL_BBL": "GROSS_OIL_BBL x net revenue interest.",
        "NET_GAS_MCF": "GROSS_GAS_MCF x net revenue interest.",
        "NET_NGL_BBL": "GROSS_NGL_BBL x net revenue interest.",
        "FORECAST_CASE": "BASE, HIGH or LOW sensitivity within the scenario.",
        "WELL_STATUS": "Forecast status on the day: PRODUCING, SHUT-IN or NOT ONLINE.",
    },

}

def describe(table: str, column: str) -> str:
    """Best description available for one column, or an empty string if the
    column is undocumented -- which the docs build reports rather than hides."""
    own = TABLES.get(table, {})
    if column in own:
        return own[column]
    return COMMON.get(column, "")


# The single most important line of documentation per table: what one row is.
GRAIN = {
    "wm.cct_well_master": "One row per well. 2,003 rows: 603 operated, 1,400 outside-operated.",
    "pc.pc_completion": "One row per ProCount completion. Operated wells only, 603 rows.",
    "pc.well_daily_prod": ("One row per completion per production day, from first "
                           "production to the latest reported day. Shut-in days are "
                           "present and carry zeros."),
    "pc.pc_daily_downtime": ("One row per completion per day per deferment reason. "
                             "Sparse -- only days that lost time appear."),
    "ac.AC_PROPERTY": "One row per ARIES property. Operated wells only, 603 rows.",
    "ac.AC_DAILY": ("One row per property per calendar day for all of 2026, including "
                    "days before first production and after shut-in, both zero."),
}


TABLE_NOTES = {
    "wm.cct_well_master": ("The corporate well header of record. Every other system "
                           "carries a subset of these attributes and they are allowed to "
                           "disagree -- reconciling them is a real task, not a bug."),
    "pc.pc_completion": ("ProCount's registry of completions it accounts for. Operated "
                         "wells only. The MERRICK_ID -> API_UWI mapping lives here and "
                         "nowhere else."),
    "pc.well_daily_prod": ("The actual sales volumes. One row per completion per day from "
                           "first production to the latest reported day. Rows are restated "
                           "as allocation firms up."),
    "pc.pc_daily_downtime": ("Deferment detail. Only days that actually lost time have "
                             "rows, so this table is sparse."),
    "ac.AC_PROPERTY": ("ARIES property master. Denormalises most of the well header and "
                       "carries the Arps constants the forecast is generated from."),
    "ac.AC_DAILY": ("The daily forecast for calendar 2026, operated wells only. Shut-in "
                    "and not-yet-online days carry explicit zeros rather than missing rows."),
}
