"""The one place that knows what the three source databases contain.

The pages, the change-feed strategies, the seeder and the documentation builder
all read this. If a column is added to a source table it goes in the DDL and in
COLUMNS here, and every other layer picks it up without edits.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent.parent / "db"

STRATEGIES = ("FULL", "INCREMENTAL", "CT", "CDC")

# Company constants -- CCT Oil & Cattle, Texas.
COMPANY = "CCT Oil & Cattle"
OPERATED_WELLS = 603
NON_OPERATED_WELLS = 1400
PRODUCTION_START = "2026-01-01"
FORECAST_END = "2026-12-31"


@dataclass(frozen=True)
class TableSpec:
    """One source table, described the way an ingestion tool needs it:
    grain, primary key, the columns worth carrying, and the audit column a
    watermarked load would key off."""
    system: str
    schema: str
    name: str
    label: str
    pk: tuple[str, ...]
    columns: tuple[str, ...]        # business columns an ingestion tool would carry
    audit_col: str = "UPDATED_TS"

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def select_columns(self) -> tuple[str, ...]:
        """Business columns plus the audit column the watermark strategy reads."""
        return self.columns + (self.audit_col,)

    @property
    def capture_instance(self) -> str:
        """CDC capture instance name: schema_table, as sp_cdc_enable_table builds it."""
        return f"{self.schema}_{self.name}"


@dataclass(frozen=True)
class SystemSpec:
    key: str
    label: str
    short: str
    vendor: str
    default_database: str
    schema: str
    ddl_file: str
    tables: tuple[TableSpec, ...]
    purpose: str          # what this system IS, in one plain sentence
    blurb: str            # the same thing for a data engineer, with the keys

    @property
    def ddl_path(self) -> Path:
        return DB_DIR / self.ddl_file

    def table(self, name: str) -> TableSpec:
        for t in self.tables:
            if t.name.lower() == name.lower():
                return t
        raise KeyError(f"{self.key} has no table {name!r}")


# ---------------------------------------------------------------------------
# WELL MASTER
# ---------------------------------------------------------------------------
WM_MASTER = TableSpec(
    system="wellmaster", schema="wm", name="cct_well_master",
    label="Well Master",
    pk=("API_UWI",),
    columns=(
        "API_UWI", "WELL_NAME", "WELL_NUMBER", "OPERATOR_NAME", "OPERATED_FLAG",
        "AREA", "PAD_NAME", "CENTRAL_FACILITY", "FIELD_NAME", "COUNTY", "STATE_CODE",
        "BASIN", "RESERVOIR", "WELL_STATUS", "WELL_TYPE",
        "SURFACE_LATITUDE", "SURFACE_LONGITUDE", "BOTTOMHOLE_LATITUDE", "BOTTOMHOLE_LONGITUDE",
        "SPUD_DATE", "COMPLETION_DATE", "FIRST_PROD_DATE",
        "LATERAL_LENGTH_FT", "TOTAL_DEPTH_FT", "WORKING_INTEREST", "NET_REVENUE_INTEREST",
    ),
)

WELLMASTER = SystemSpec(
    key="wellmaster", label="Well Master", short="WM", vendor="In-house master data",
    default_database="WELL_MASTER_DB", schema="wm", ddl_file="10_wellmaster.sql",
    tables=(WM_MASTER,),
    purpose=("Where a well is and who owns it. The well information system: name, "
             "location, pad and facility, county and basin, spud and completion "
             "dates, and the title interests -- working interest and net revenue "
             "interest. No volumes of any kind live here."),
    blurb=("The corporate well header of record. One slim row per well keyed by the "
           "14-digit API/UWI, covering 603 operated and 1,400 non-operated wells "
           "across Texas."),
)

# ---------------------------------------------------------------------------
# PROCOUNT
# ---------------------------------------------------------------------------
PC_COMPLETION = TableSpec(
    system="procount", schema="pc", name="pc_completion",
    label="Completion master",
    pk=("MERRICK_ID",),
    columns=(
        "MERRICK_ID", "API_UWI", "COMPLETION_NAME", "PROD_ROUTE", "BATTERY_ID",
        "BATTERY_NAME", "AREA", "ACTIVE_FLAG", "FIRST_PROD_DATE", "ALLOC_METHOD",
    ),
)

PC_DAILY = TableSpec(
    system="procount", schema="pc", name="well_daily_prod",
    label="Daily allocated production",
    pk=("MERRICK_ID", "PROD_DATE"),
    columns=(
        "MERRICK_ID", "PROD_DATE", "API_UWI",
        "OIL_BBL", "GAS_MCF", "WATER_BBL", "NGL_BBL",
        "NET_OIL_BBL", "NET_GAS_MCF", "NET_NGL_BBL",
        "PRODUCING_HOURS", "DOWNTIME_HOURS",
        "TUBING_PRESSURE_PSI", "CASING_PRESSURE_PSI", "CHOKE_SIZE_64THS",
        "WELL_STATUS_CODE", "ALLOCATION_STATUS",
    ),
)

PC_DOWNTIME = TableSpec(
    system="procount", schema="pc", name="pc_daily_downtime",
    label="Downtime / deferment",
    pk=("MERRICK_ID", "DOWNTIME_DATE", "SEQ_NO"),
    columns=(
        "MERRICK_ID", "DOWNTIME_DATE", "SEQ_NO", "API_UWI", "DOWNTIME_HOURS",
        "REASON_CODE", "REASON_DESC", "DEFERRED_OIL_BBL", "DEFERRED_GAS_MCF",
    ),
)

PROCOUNT = SystemSpec(
    key="procount", label="ProCount", short="PC", vendor="Quorum / Merrick ProCount",
    default_database="PROCOUNT_DB", schema="pc", ddl_file="20_procount.sql",
    tables=(PC_COMPLETION, PC_DAILY, PC_DOWNTIME),
    purpose=("What the wells actually produced and sold. Daily production and "
             "sales volumes -- oil, gas, NGL and water, gross and net -- one row "
             "per well per day, with the downtime that explains any day that "
             "lost hours. This is actuals, not plan."),
    blurb=("Production accounting: the allocated daily sales volumes for operated "
           "wells. Keyed on MERRICK_ID, ProCount's own completion surrogate, with "
           "the API carried as an attribute."),
)

# ---------------------------------------------------------------------------
# ARIES
# ---------------------------------------------------------------------------
AC_PROPERTY = TableSpec(
    system="aries", schema="ac", name="AC_PROPERTY",
    label="Property master",
    pk=("PROPNUM",),
    columns=(
        "PROPNUM", "API_UWI", "WELL_NAME", "LEASE_NAME", "OPERATOR_NAME", "AREA",
        "PAD_NAME", "FIELD_NAME", "COUNTY", "STATE_CODE", "RESERVOIR", "MAJOR_PHASE",
        "WELL_STATUS", "FIRST_PROD_DATE", "SURFACE_LATITUDE", "SURFACE_LONGITUDE",
        "WORKING_INTEREST", "NET_REVENUE_INTEREST", "SCENARIO", "RESERVE_CAT",
        "TYPE_CURVE", "EFFECTIVE_DATE", "QI_OIL_BOPD", "DI_NOMINAL", "B_FACTOR",
    ),
)

AC_DAILY = TableSpec(
    system="aries", schema="ac", name="AC_DAILY",
    label="Daily forecast",
    pk=("PROPNUM", "D_DATE"),
    columns=(
        "PROPNUM", "D_DATE", "API_UWI", "WELL_NAME", "AREA", "PAD_NAME",
        "WELL_STATUS", "MAJOR_PHASE",
        "GROSS_OIL_BBL", "GROSS_GAS_MCF", "GROSS_WATER_BBL", "GROSS_NGL_BBL",
        "NET_OIL_BBL", "NET_GAS_MCF", "NET_NGL_BBL",
        "SCENARIO", "RESERVE_CAT", "FORECAST_CASE",
    ),
)

ARIES = SystemSpec(
    key="aries", label="ARIES", short="AC", vendor="Landmark / Halliburton ARIES",
    default_database="ARIES_DB", schema="ac", ddl_file="30_aries.sql",
    tables=(AC_PROPERTY, AC_DAILY),
    purpose=("What the wells are expected to produce -- the forecast, and so the "
             "budget. A daily type-curve projection per operated property for the "
             "whole year, restated whenever engineering re-runs a type curve. "
             "This is plan, not actuals."),
    blurb=("Reserves and forecasting. Daily type-curve forecast for operated wells "
           "only, keyed on PROPNUM, the ARIES property id."),
)

SOURCE_SYSTEMS: dict[str, SystemSpec] = {s.key: s for s in (WELLMASTER, PROCOUNT, ARIES)}
ALL_SYSTEMS = SOURCE_SYSTEMS


def system(key: str) -> SystemSpec:
    try:
        return ALL_SYSTEMS[key]
    except KeyError:
        raise ValueError(f"unknown system {key!r}") from None


def all_source_tables() -> list[TableSpec]:
    return [t for s in SOURCE_SYSTEMS.values() for t in s.tables]
