"""A transformation curriculum written against the three source systems.

The data dictionary says what the columns are and the join recipes say how the
systems fit together. This is the layer underneath both: the SQL itself, from
"SELECT a column" to a windowed month-end restatement, every example written
twice -- once in T-SQL against these SQL Server sources, once in Snowflake as it
would look after ADF or Fivetran has landed the same tables.

Every lesson is built the same way:

    Title  ->  Description  ->  SQL Server syntax  ->  Snowflake syntax

Table names are tokens so that both dialects can be written from one text and so
the T-SQL comes out three-part qualified against whatever the Connections page
actually points at. Expansion happens in ``render()``; nothing here is formatted
with str.format, so braces inside SQL are safe.

Lessons marked ``runnable=False`` write something (MERGE, CREATE VIEW, INSERT
... SELECT). The in-page runner refuses anything but a single read, so those are
shown for reading only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Table tokens
# --------------------------------------------------------------------------
# The Snowflake side matches documentation/joins.py: one database per source
# system, schema and table names carried over unchanged and upper-cased.
SNOWFLAKE_TABLES = {
    "{WM}":       "WELL_MASTER.WM.CCT_WELL_MASTER",
    "{PC_COMP}":  "PROCOUNT.PC.PC_COMPLETION",
    "{PC_DAILY}": "PROCOUNT.PC.WELL_DAILY_PROD",
    "{PC_DT}":    "PROCOUNT.PC.PC_DAILY_DOWNTIME",
    "{AC_PROP}":  "ARIES.AC.AC_PROPERTY",
    "{AC_DAILY}": "ARIES.AC.AC_DAILY",
}

# Database on its own, for the lessons that read INFORMATION_SCHEMA rather than
# a table.
SNOWFLAKE_DATABASES = {
    "{WM_DB}": "WELL_MASTER",
    "{PC_DB}": "PROCOUNT",
    "{AC_DB}": "ARIES",
}

# The SQL Server side is three-part qualified so a query runs from any of the
# three connections. The database name comes from the live connection profile,
# because the Connections page is allowed to change it.
_SQLSERVER_SUFFIX = {
    "{WM}":       ("wellmaster", "wm.cct_well_master"),
    "{PC_COMP}":  ("procount",   "pc.pc_completion"),
    "{PC_DAILY}": ("procount",   "pc.well_daily_prod"),
    "{PC_DT}":    ("procount",   "pc.pc_daily_downtime"),
    "{AC_PROP}":  ("aries",      "ac.AC_PROPERTY"),
    "{AC_DAILY}": ("aries",      "ac.AC_DAILY"),
}

_SQLSERVER_DATABASES = {
    "{WM_DB}": "wellmaster",
    "{PC_DB}": "procount",
    "{AC_DB}": "aries",
}

LEVELS = ("Basic", "Intermediate")


@dataclass(frozen=True)
class Lesson:
    slug: str
    title: str
    level: str                     # Basic | Intermediate
    description: str
    tsql: str
    snowflake: str
    notes: tuple[str, ...] = ()
    runnable: bool = True
    system: str = "procount"       # which connection the runner should use


@dataclass(frozen=True)
class Section:
    slug: str
    title: str
    blurb: str
    lessons: tuple[Lesson, ...] = field(default_factory=tuple)


def _expand(sql: str, tokens: dict[str, str]) -> str:
    for token, replacement in tokens.items():
        sql = sql.replace(token, replacement)
    return sql


def sqlserver_tokens(database_for) -> dict[str, str]:
    """Token map for T-SQL. ``database_for(system_key)`` returns a database name."""
    tokens = {tok: f"{database_for(system)}.{rest}"
              for tok, (system, rest) in _SQLSERVER_SUFFIX.items()}
    tokens.update({tok: database_for(system)
                   for tok, system in _SQLSERVER_DATABASES.items()})
    return tokens


def render(database_for) -> list[dict]:
    """Sections with both dialects expanded, ready for the template."""
    tsql_tokens = sqlserver_tokens(database_for)
    out = []
    for sec in SECTIONS:
        out.append({
            "slug": sec.slug, "title": sec.title, "blurb": sec.blurb,
            "lessons": [{
                "slug": ls.slug, "title": ls.title, "level": ls.level,
                "description": ls.description,
                "tsql": _expand(ls.tsql, tsql_tokens),
                "snowflake": _expand(ls.snowflake,
                                     {**SNOWFLAKE_TABLES, **SNOWFLAKE_DATABASES}),
                "notes": list(ls.notes),
                "runnable": ls.runnable, "system": ls.system,
            } for ls in sec.lessons],
        })
    return out


def all_lessons() -> list[Lesson]:
    return [ls for sec in SECTIONS for ls in sec.lessons]


# ==========================================================================
# 1 -- Reading one table
# ==========================================================================
S_SELECT = Section(
    slug="select",
    title="Reading one table",
    blurb=("Everything starts here. Pick the columns you actually need, name them, "
           "sort them, and stop the result set being 220,000 rows long while you are "
           "still exploring."),
    lessons=(
        Lesson(
            slug="select-columns",
            title="SELECT -- pick columns and name them",
            level="Basic",
            system="procount",
            description=(
                "A SELECT list is the first transformation you ever write: it drops the "
                "columns you do not want and renames the ones you keep. Name every derived "
                "column with AS, because an unnamed expression lands in the warehouse as a "
                "column called something like EXPR$3."),
            tsql="""SELECT TOP 20
       MERRICK_ID,
       PROD_DATE,
       API_UWI,
       OIL_BBL       AS GROSS_OIL_BBL,
       GAS_MCF       AS GROSS_GAS_MCF,
       ALLOCATION_STATUS
  FROM {PC_DAILY}
 ORDER BY PROD_DATE DESC, MERRICK_ID;""",
            snowflake="""SELECT MERRICK_ID,
       PROD_DATE,
       API_UWI,
       OIL_BBL       AS GROSS_OIL_BBL,
       GAS_MCF       AS GROSS_GAS_MCF,
       ALLOCATION_STATUS
  FROM {PC_DAILY}
 ORDER BY PROD_DATE DESC, MERRICK_ID
 LIMIT 20;""",
            notes=(
                "SELECT * is fine at a prompt and wrong in a pipeline: a column added "
                "upstream silently changes the shape of everything downstream.",
                "Column order in a SELECT list is a contract. UNION matches by position, "
                "not by name, and so does INSERT ... SELECT.",
            ),
        ),
        Lesson(
            slug="select-top",
            title="TOP and LIMIT -- cutting a sample",
            level="Basic",
            system="aries",
            description=(
                "The one piece of everyday syntax that is genuinely different in the two "
                "engines. SQL Server puts the row limit at the front with TOP; Snowflake "
                "puts it at the back with LIMIT. Both need an ORDER BY to mean anything -- "
                "without one you get an arbitrary 20 rows, not the first 20."),
            tsql="""SELECT TOP 20
       PROPNUM, D_DATE, WELL_NAME, GROSS_OIL_BBL
  FROM {AC_DAILY}
 ORDER BY GROSS_OIL_BBL DESC;""",
            snowflake="""SELECT PROPNUM, D_DATE, WELL_NAME, GROSS_OIL_BBL
  FROM {AC_DAILY}
 ORDER BY GROSS_OIL_BBL DESC
 LIMIT 20;

-- Paging: rows 21-40.
SELECT PROPNUM, D_DATE, WELL_NAME, GROSS_OIL_BBL
  FROM {AC_DAILY}
 ORDER BY GROSS_OIL_BBL DESC
 LIMIT 20 OFFSET 20;""",
            notes=(
                "SQL Server pages with OFFSET 20 ROWS FETCH NEXT 20 ROWS ONLY, which "
                "requires an ORDER BY. Snowflake writes LIMIT 20 OFFSET 20.",
                "Snowflake accepts TOP n for compatibility, but LIMIT is the idiom.",
            ),
        ),
        Lesson(
            slug="select-distinct",
            title="SELECT DISTINCT -- what values does this column actually hold?",
            level="Basic",
            system="wellmaster",
            description=(
                "Before you write a CASE or a filter, find out what is in the column. "
                "DISTINCT over one or two columns is the fastest profiling tool there is, "
                "and it is how you discover that ARIES spells an area name differently "
                "from the well master."),
            tsql="""SELECT DISTINCT AREA, BASIN
  FROM {WM}
 ORDER BY BASIN, AREA;""",
            snowflake="""SELECT DISTINCT AREA, BASIN
  FROM {WM}
 ORDER BY BASIN, AREA;""",
            notes=(
                "DISTINCT applies to the whole SELECT list, never to one column of it. "
                "SELECT DISTINCT AREA, WELL_NAME gives you every well, not every area.",
                "For a count of distinct values use COUNT(DISTINCT col) instead -- it does "
                "not drag the values back to the client.",
            ),
        ),
    ),
)

# ==========================================================================
# 2 -- Filtering rows
# ==========================================================================
S_FILTER = Section(
    slug="filter",
    title="Filtering rows",
    blurb=("WHERE decides which rows survive. Almost every ingestion bug that ends in "
           "a wrong number is a predicate that was subtly wider or narrower than its "
           "author thought, so these are worth being pedantic about."),
    lessons=(
        Lesson(
            slug="where-basic",
            title="WHERE -- the basic predicate",
            level="Basic",
            system="procount",
            description=(
                "Restrict rows by comparing a column to a value. Note the date literal: "
                "both engines accept an ISO 'YYYY-MM-DD' string and cast it for you, and "
                "ISO is the only format that is unambiguous regardless of the server's "
                "language setting."),
            tsql="""SELECT TOP 50 MERRICK_ID, PROD_DATE, OIL_BBL, GAS_MCF, WELL_STATUS_CODE
  FROM {PC_DAILY}
 WHERE PROD_DATE = '2026-06-15'
   AND OIL_BBL > 0
 ORDER BY OIL_BBL DESC;""",
            snowflake="""SELECT MERRICK_ID, PROD_DATE, OIL_BBL, GAS_MCF, WELL_STATUS_CODE
  FROM {PC_DAILY}
 WHERE PROD_DATE = '2026-06-15'
   AND OIL_BBL > 0
 ORDER BY OIL_BBL DESC
 LIMIT 50;""",
            notes=(
                "PROD_DATE is a date, not a datetime, so equality works. On a datetime "
                "column always write a half-open range -- >= '2026-06-15' AND "
                "< '2026-06-16' -- never = and never BETWEEN.",
                "Wrapping a column in a function (WHERE YEAR(PROD_DATE) = 2026) makes the "
                "predicate non-sargable and throws the index away. Put the function on the "
                "literal side instead.",
            ),
        ),
        Lesson(
            slug="where-and-or",
            title="AND, OR, NOT -- and the parentheses everyone forgets",
            level="Basic",
            system="wellmaster",
            description=(
                "AND binds tighter than OR. 'Operated wells in Midland Basin Core or "
                "Midland Basin South' without brackets quietly becomes 'operated wells in "
                "the Core, plus every well anywhere in the South' -- and the row count "
                "still looks reasonable, which is what makes it dangerous."),
            tsql="""SELECT API_UWI, WELL_NAME, AREA, WELL_STATUS, OPERATED_FLAG
  FROM {WM}
 WHERE OPERATED_FLAG = 1
   AND (AREA = 'Midland Basin Core' OR AREA = 'Midland Basin South')
   AND WELL_STATUS <> 'SHUT-IN'
 ORDER BY AREA, WELL_NAME;""",
            snowflake="""SELECT API_UWI, WELL_NAME, AREA, WELL_STATUS, OPERATED_FLAG
  FROM {WM}
 WHERE OPERATED_FLAG = TRUE
   AND (AREA = 'Midland Basin Core' OR AREA = 'Midland Basin South')
   AND WELL_STATUS <> 'SHUT-IN'
 ORDER BY AREA, WELL_NAME;""",
            notes=(
                "OPERATED_FLAG is a SQL Server bit and lands in Snowflake as BOOLEAN. "
                "= 1 works on the source; = TRUE, or just the bare column, after landing.",
                "col <> value excludes NULLs as well as matches, because NULL <> value is "
                "unknown, not true. If NULLs should survive, say so: "
                "(col <> value OR col IS NULL).",
            ),
        ),
        Lesson(
            slug="where-in-between",
            title="IN and BETWEEN -- sets and ranges",
            level="Basic",
            system="procount",
            description=(
                "IN is a tidier OR chain. BETWEEN is inclusive at both ends, which is "
                "exactly what you want on a date column and exactly what you do not want "
                "on a timestamp. This particular filter is worth remembering: it is the "
                "set of rows that are still allowed to change."),
            tsql="""SELECT MERRICK_ID, PROD_DATE, OIL_BBL, ALLOCATION_STATUS
  FROM {PC_DAILY}
 WHERE ALLOCATION_STATUS IN ('ESTIMATED', 'ALLOCATED')
   AND PROD_DATE BETWEEN DATEADD(day, -35, CAST(GETDATE() AS date))
                     AND CAST(GETDATE() AS date)
 ORDER BY PROD_DATE DESC, MERRICK_ID;""",
            snowflake="""SELECT MERRICK_ID, PROD_DATE, OIL_BBL, ALLOCATION_STATUS
  FROM {PC_DAILY}
 WHERE ALLOCATION_STATUS IN ('ESTIMATED', 'ALLOCATED')
   AND PROD_DATE BETWEEN DATEADD('day', -35, CURRENT_DATE)
                     AND CURRENT_DATE
 ORDER BY PROD_DATE DESC, MERRICK_ID;""",
            notes=(
                "NOT IN against a subquery that can return NULL returns no rows at all, "
                "ever. Use NOT EXISTS -- see the anti-join lesson.",
                "BETWEEN on a datetime2 column loses the last day, because '2026-06-30' "
                "means midnight. Half-open ranges do not have this problem.",
            ),
        ),
        Lesson(
            slug="where-like-null",
            title="LIKE and IS NULL -- patterns, and the absence of a value",
            level="Basic",
            system="wellmaster",
            description=(
                "LIKE matches a pattern: % is any run of characters, _ is exactly one. "
                "NULL is not a value, so it is never equal to anything -- including itself "
                "-- and only IS NULL will find it."),
            tsql="""SELECT API_UWI, WELL_NAME, COUNTY, CENTRAL_FACILITY, PAD_NAME
  FROM {WM}
 WHERE WELL_NAME LIKE '%RANCH%'          -- anywhere in the name
   AND API_UWI  LIKE '42317%'            -- Texas (42) + Martin County (317)
   AND CENTRAL_FACILITY IS NOT NULL
 ORDER BY WELL_NAME;""",
            snowflake="""SELECT API_UWI, WELL_NAME, COUNTY, CENTRAL_FACILITY, PAD_NAME
  FROM {WM}
 WHERE WELL_NAME ILIKE '%ranch%'         -- ILIKE = case-insensitive
   AND API_UWI  LIKE '42317%'
   AND CENTRAL_FACILITY IS NOT NULL
 ORDER BY WELL_NAME;""",
            notes=(
                "This is the biggest behavioural difference between the two engines. SQL "
                "Server compares strings case-insensitively by default (collation); "
                "Snowflake is case-sensitive. A filter that worked on the source can return "
                "nothing after landing -- use ILIKE, or UPPER() on both sides.",
                "A leading % cannot use an index. Against 2,003 wells nobody notices; "
                "against 128,000 daily rows, they do.",
            ),
        ),
    ),
)

# ==========================================================================
# 3 -- Shaping values
# ==========================================================================
S_SHAPE = Section(
    slug="shape",
    title="Shaping values",
    blurb=("Turning what the source stores into what the business asks for: derived "
           "measures, banded categories, defaulted NULLs, parsed keys and truncated "
           "dates. This is where most of the actual transformation lives."),
    lessons=(
        Lesson(
            slug="derived-columns",
            title="Arithmetic -- deriving BOE and uptime",
            level="Basic",
            system="procount",
            description=(
                "The source stores oil, gas, NGL and hours; the business asks for barrels "
                "of oil equivalent and an uptime percentage. Both are pure expressions over "
                "columns already on the row, which makes them the cheapest kind of "
                "transformation there is."),
            tsql="""SELECT TOP 50
       MERRICK_ID,
       PROD_DATE,
       OIL_BBL,
       GAS_MCF,
       NGL_BBL,
       OIL_BBL + NGL_BBL + GAS_MCF / 6.0            AS BOE,
       PRODUCING_HOURS / 24.0 * 100                 AS UPTIME_PCT,
       CAST(GAS_MCF / NULLIF(OIL_BBL, 0)            -- guard the divide
            AS decimal(12,2))                       AS GOR_MCF_PER_BBL
  FROM {PC_DAILY}
 WHERE PROD_DATE = '2026-06-15'
 ORDER BY BOE DESC;""",
            snowflake="""SELECT MERRICK_ID,
       PROD_DATE,
       OIL_BBL,
       GAS_MCF,
       NGL_BBL,
       OIL_BBL + NGL_BBL + GAS_MCF / 6.0            AS BOE,
       PRODUCING_HOURS / 24.0 * 100                 AS UPTIME_PCT,
       ROUND(GAS_MCF / NULLIF(OIL_BBL, 0), 2)       AS GOR_MCF_PER_BBL
  FROM {PC_DAILY}
 WHERE PROD_DATE = '2026-06-15'
 ORDER BY BOE DESC
 LIMIT 50;""",
            notes=(
                "NULLIF(x, 0) is the standard divide-by-zero guard in both engines: it "
                "turns the zero into NULL, and anything divided by NULL is NULL. SQL Server "
                "raises an error without it; Snowflake also raises a division-by-zero.",
                "6 Mcf to 1 BOE is a volumetric convention, not an energy or value one. "
                "Agree the ratio with finance before publishing it.",
                "Do not mix NET_ and gross volumes in one measure -- NET_ is already "
                "multiplied by the net revenue interest.",
            ),
        ),
        Lesson(
            slug="case-when",
            title="CASE -- banding and re-coding",
            level="Basic",
            system="wellmaster",
            description=(
                "CASE is how a continuous column becomes a category and how two systems' "
                "vocabularies get conformed. It evaluates top to bottom and stops at the "
                "first match, so the order of the WHEN branches is the logic."),
            tsql="""SELECT API_UWI,
       WELL_NAME,
       LATERAL_LENGTH_FT,
       CASE WHEN LATERAL_LENGTH_FT >= 12000 THEN '3-mile'
            WHEN LATERAL_LENGTH_FT >=  9000 THEN '2-mile'
            WHEN LATERAL_LENGTH_FT >=  4500 THEN '1-mile'
            ELSE 'short / vertical'
       END                                          AS LATERAL_CLASS,
       CASE WHEN OPERATED_FLAG = 1 THEN 'OPERATED'
            ELSE 'NON-OPERATED'
       END                                          AS OPERATORSHIP,
       IIF(WELL_STATUS = 'SHUT-IN', 1, 0)           AS IS_SHUT_IN
  FROM {WM}
 ORDER BY LATERAL_LENGTH_FT DESC;""",
            snowflake="""SELECT API_UWI,
       WELL_NAME,
       LATERAL_LENGTH_FT,
       CASE WHEN LATERAL_LENGTH_FT >= 12000 THEN '3-mile'
            WHEN LATERAL_LENGTH_FT >=  9000 THEN '2-mile'
            WHEN LATERAL_LENGTH_FT >=  4500 THEN '1-mile'
            ELSE 'short / vertical'
       END                                          AS LATERAL_CLASS,
       IFF(OPERATED_FLAG, 'OPERATED', 'NON-OPERATED') AS OPERATORSHIP,
       IFF(WELL_STATUS = 'SHUT-IN', 1, 0)           AS IS_SHUT_IN
  FROM {WM}
 ORDER BY LATERAL_LENGTH_FT DESC;""",
            notes=(
                "The two-way shorthand is IIF in SQL Server and IFF in Snowflake -- one F "
                "apart, and easy to miss in a port. Plain CASE works identically in both, "
                "which is why it is the safer thing to write in shared code.",
                "Leaving the ELSE off means unmatched rows get NULL, not zero. That is "
                "usually a bug in a banding expression and always a bug in a flag.",
            ),
        ),
        Lesson(
            slug="null-handling",
            title="COALESCE, ISNULL and NULLIF -- defaulting missing values",
            level="Basic",
            system="procount",
            description=(
                "After a LEFT JOIN, missing means NULL, and NULL propagates through every "
                "arithmetic expression it touches. COALESCE returns the first non-NULL of "
                "its arguments and is ANSI standard, so it is the one to reach for."),
            tsql="""SELECT TOP 50
       d.MERRICK_ID,
       d.PROD_DATE,
       d.OIL_BBL,
       COALESCE(dt.DOWNTIME_HOURS, 0)               AS EVENT_DOWNTIME_HOURS,
       ISNULL(dt.REASON_CODE, 'NONE')               AS REASON_CODE,
       COALESCE(d.TUBING_PRESSURE_PSI, d.CASING_PRESSURE_PSI, 0) AS PRESSURE_PSI
  FROM {PC_DAILY} d
  LEFT JOIN {PC_DT} dt
         ON dt.MERRICK_ID = d.MERRICK_ID
        AND dt.DOWNTIME_DATE = d.PROD_DATE
        AND dt.SEQ_NO = 1
 WHERE d.PROD_DATE = '2026-06-15'
 ORDER BY EVENT_DOWNTIME_HOURS DESC;""",
            snowflake="""SELECT d.MERRICK_ID,
       d.PROD_DATE,
       d.OIL_BBL,
       COALESCE(dt.DOWNTIME_HOURS, 0)               AS EVENT_DOWNTIME_HOURS,
       IFNULL(dt.REASON_CODE, 'NONE')               AS REASON_CODE,
       COALESCE(d.TUBING_PRESSURE_PSI, d.CASING_PRESSURE_PSI, 0) AS PRESSURE_PSI
  FROM {PC_DAILY} d
  LEFT JOIN {PC_DT} dt
         ON dt.MERRICK_ID = d.MERRICK_ID
        AND dt.DOWNTIME_DATE = d.PROD_DATE
        AND dt.SEQ_NO = 1
 WHERE d.PROD_DATE = '2026-06-15'
 ORDER BY EVENT_DOWNTIME_HOURS DESC
 LIMIT 50;""",
            notes=(
                "ISNULL is SQL Server only and takes exactly two arguments; IFNULL is the "
                "Snowflake spelling. COALESCE takes any number and works in both.",
                "No downtime row means no lost time, not unknown time. COALESCE to 0 is "
                "correct here. On a pressure reading, defaulting an unknown to 0 would be "
                "an invented measurement -- know which case you are in.",
            ),
        ),
        Lesson(
            slug="cast-convert",
            title="CAST -- types, rounding and safe conversion",
            level="Basic",
            system="aries",
            description=(
                "Types change on the way into the warehouse: decimal(12,2) becomes "
                "NUMBER(12,2), datetime2(3) becomes TIMESTAMP_NTZ(3). Cast explicitly "
                "where it matters instead of relying on the engine's implicit rules, which "
                "differ."),
            tsql="""SELECT TOP 50
       PROPNUM,
       D_DATE,
       CAST(GROSS_OIL_BBL AS decimal(12,1))         AS OIL_1DP,
       ROUND(GROSS_GAS_MCF, 0)                      AS GAS_WHOLE_MCF,
       CAST(D_DATE AS varchar(10))                  AS D_DATE_TEXT,
       TRY_CAST(RESERVE_CAT AS int)                 AS NEVER_A_NUMBER  -- NULL, not an error
  FROM {AC_DAILY}
 WHERE D_DATE = '2026-06-15'
 ORDER BY OIL_1DP DESC;""",
            snowflake="""SELECT PROPNUM,
       D_DATE,
       CAST(GROSS_OIL_BBL AS NUMBER(12,1))          AS OIL_1DP,
       ROUND(GROSS_GAS_MCF, 0)                      AS GAS_WHOLE_MCF,
       TO_VARCHAR(D_DATE, 'YYYY-MM-DD')             AS D_DATE_TEXT,
       TRY_CAST(RESERVE_CAT AS INT)                 AS NEVER_A_NUMBER
  FROM {AC_DAILY}
 WHERE D_DATE = '2026-06-15'
 ORDER BY OIL_1DP DESC
 LIMIT 50;""",
            notes=(
                "CAST to a shorter decimal rounds, it does not truncate. Casting a date to "
                "varchar in SQL Server without a style code is locale-dependent; "
                "CONVERT(varchar(10), D_DATE, 23) or Snowflake's explicit format string is "
                "the safe form.",
                "TRY_CAST exists in both and returns NULL instead of failing the batch. In "
                "an ingestion pipeline that is usually what you want, paired with a check "
                "that counts how many rows went NULL.",
            ),
        ),
        Lesson(
            slug="string-functions",
            title="String functions -- taking the API apart",
            level="Basic",
            system="wellmaster",
            description=(
                "A 14-digit API/UWI is not one value, it is four: state, county, unique "
                "well, and the sidetrack/completion suffix. Splitting it is the standard "
                "worked example of SUBSTRING, LEN, CONCAT and TRIM -- and it is also a real "
                "data quality check, because characters 3-5 must match the county."),
            tsql="""SELECT TOP 50
       API_UWI,
       SUBSTRING(API_UWI, 1, 2)                     AS STATE_CODE_API,
       SUBSTRING(API_UWI, 3, 3)                     AS COUNTY_CODE_API,
       SUBSTRING(API_UWI, 6, 5)                     AS UNIQUE_WELL,
       SUBSTRING(API_UWI, 11, 4)                    AS SIDETRACK_SUFFIX,
       LEN(API_UWI)                                 AS API_LEN,
       UPPER(LTRIM(RTRIM(WELL_NAME)))               AS WELL_NAME_CLEAN,
       CONCAT(WELL_NAME, ' ', WELL_NUMBER)          AS FULL_WELL_NAME,
       REPLACE(AREA, 'Midland Basin ', 'MB-')       AS AREA_SHORT
  FROM {WM}
 WHERE OPERATED_FLAG = 1
 ORDER BY API_UWI;""",
            snowflake="""SELECT API_UWI,
       SUBSTR(API_UWI, 1, 2)                        AS STATE_CODE_API,
       SUBSTR(API_UWI, 3, 3)                        AS COUNTY_CODE_API,
       SUBSTR(API_UWI, 6, 5)                        AS UNIQUE_WELL,
       SUBSTR(API_UWI, 11, 4)                       AS SIDETRACK_SUFFIX,
       LENGTH(API_UWI)                              AS API_LEN,
       UPPER(TRIM(WELL_NAME))                       AS WELL_NAME_CLEAN,
       CONCAT(WELL_NAME, ' ', WELL_NUMBER)          AS FULL_WELL_NAME,
       REPLACE(AREA, 'Midland Basin ', 'MB-')       AS AREA_SHORT
  FROM {WM}
 WHERE OPERATED_FLAG
 ORDER BY API_UWI
 LIMIT 50;""",
            notes=(
                "LEN in SQL Server ignores trailing spaces; LENGTH in Snowflake does not. "
                "DATALENGTH/LEN differences bite when you are validating fixed-width keys, "
                "so TRIM first.",
                "SQL Server has LTRIM/RTRIM (and TRIM from 2017); Snowflake has TRIM, LTRIM "
                "and RTRIM. Both support || as a concatenation operator, but SQL Server "
                "spells it +, which is another silent porting trap.",
                "Keep the API as a string. Casting it to a number loses the leading digits "
                "of the county code and every check downstream stops working.",
            ),
        ),
        Lesson(
            slug="date-functions",
            title="Date functions -- truncating, adding and differencing",
            level="Basic",
            system="procount",
            description=(
                "Reporting is almost always a date roll-up, and dates are where the two "
                "dialects diverge most. Snowflake has DATE_TRUNC and a DATEADD whose "
                "arguments are quoted; SQL Server has no DATE_TRUNC before 2022 and uses "
                "bare keyword date parts."),
            tsql="""SELECT TOP 50
       PROD_DATE,
       DATEFROMPARTS(YEAR(PROD_DATE), MONTH(PROD_DATE), 1) AS PROD_MONTH,
       EOMONTH(PROD_DATE)                            AS MONTH_END,
       YEAR(PROD_DATE)                               AS PROD_YEAR,
       DATEPART(quarter, PROD_DATE)                  AS PROD_QUARTER,
       DATENAME(weekday, PROD_DATE)                  AS DAY_NAME,
       DATEADD(day, -30, PROD_DATE)                  AS THIRTY_DAYS_BEFORE,
       DATEDIFF(day, '2026-01-01', PROD_DATE)        AS DAYS_SINCE_START
  FROM {PC_DAILY}
 WHERE PROD_DATE >= DATEADD(day, -7, '2026-06-15')
 ORDER BY PROD_DATE DESC;""",
            snowflake="""SELECT PROD_DATE,
       DATE_TRUNC('month', PROD_DATE)                AS PROD_MONTH,
       LAST_DAY(PROD_DATE)                           AS MONTH_END,
       YEAR(PROD_DATE)                               AS PROD_YEAR,
       QUARTER(PROD_DATE)                            AS PROD_QUARTER,
       DAYNAME(PROD_DATE)                            AS DAY_NAME,
       DATEADD('day', -30, PROD_DATE)                AS THIRTY_DAYS_BEFORE,
       DATEDIFF('day', '2026-01-01', PROD_DATE)      AS DAYS_SINCE_START
  FROM {PC_DAILY}
 WHERE PROD_DATE >= DATEADD('day', -7, DATE '2026-06-15')
 ORDER BY PROD_DATE DESC
 LIMIT 50;""",
            notes=(
                "DATEDIFF argument order is the same in both (unit, start, end), and both "
                "count boundaries crossed, not elapsed time: DATEDIFF(day, '2026-01-01 "
                "23:59', '2026-01-02 00:01') is 1.",
                "SQL Server 2022 added DATE_TRUNC. On anything older, DATEFROMPARTS(...) "
                "for a month start is the portable trick.",
                "PROD_DATE and D_DATE are wall-clock production dates with no timezone. "
                "CREATED_TS and UPDATED_TS are UTC. Do not shift either on the way in.",
            ),
        ),
    ),
)

# ==========================================================================
# 4 -- Aggregating
# ==========================================================================
S_AGG = Section(
    slug="aggregate",
    title="Aggregating",
    blurb=("Collapsing many rows into one. The whole of production reporting is "
           "GROUP BY with a well-chosen grain, and the whole of reconciliation is "
           "counting the same thing two ways and comparing."),
    lessons=(
        Lesson(
            slug="aggregate-functions",
            title="COUNT, SUM, AVG, MIN, MAX -- the whole table as one row",
            level="Basic",
            system="procount",
            description=(
                "With no GROUP BY, an aggregate collapses the entire result set to a single "
                "row. That makes it the first thing to run against a newly landed table: "
                "row count, date range, and whether the volumes are the right order of "
                "magnitude."),
            tsql="""SELECT COUNT(*)                    AS ROW_COUNT,
       COUNT(DISTINCT MERRICK_ID)     AS WELLS,
       MIN(PROD_DATE)                 AS FIRST_DAY,
       MAX(PROD_DATE)                 AS LAST_DAY,
       SUM(OIL_BBL)                   AS TOTAL_OIL_BBL,
       AVG(OIL_BBL)                   AS AVG_OIL_BBL,
       MAX(OIL_BBL)                   AS BEST_WELL_DAY,
       SUM(CASE WHEN OIL_BBL = 0 THEN 1 ELSE 0 END) AS ZERO_OIL_DAYS
  FROM {PC_DAILY};""",
            snowflake="""SELECT COUNT(*)                    AS ROW_COUNT,
       COUNT(DISTINCT MERRICK_ID)     AS WELLS,
       MIN(PROD_DATE)                 AS FIRST_DAY,
       MAX(PROD_DATE)                 AS LAST_DAY,
       SUM(OIL_BBL)                   AS TOTAL_OIL_BBL,
       AVG(OIL_BBL)                   AS AVG_OIL_BBL,
       MAX(OIL_BBL)                   AS BEST_WELL_DAY,
       COUNT_IF(OIL_BBL = 0)          AS ZERO_OIL_DAYS
  FROM {PC_DAILY};""",
            notes=(
                "COUNT(*) counts rows; COUNT(col) counts rows where col is not NULL. The "
                "difference between the two is a free NULL audit.",
                "Every aggregate except COUNT(*) ignores NULLs, so AVG over a column with "
                "missing values is the average of what is present -- which is often not the "
                "average anyone asked for.",
                "SUM(CASE WHEN ... THEN 1 ELSE 0 END) is the portable conditional count. "
                "Snowflake's COUNT_IF is shorter but does not exist in SQL Server.",
            ),
        ),
        Lesson(
            slug="group-by",
            title="GROUP BY -- one row per group",
            level="Basic",
            system="procount",
            description=(
                "GROUP BY sets the grain of the output. Every column in the SELECT list is "
                "either in the GROUP BY or inside an aggregate -- there is no third option, "
                "and both engines will refuse the query if you try."),
            tsql="""SELECT DATEFROMPARTS(YEAR(PROD_DATE), MONTH(PROD_DATE), 1) AS PROD_MONTH,
       ALLOCATION_STATUS,
       COUNT(*)                       AS WELL_DAYS,
       COUNT(DISTINCT MERRICK_ID)     AS WELLS,
       SUM(OIL_BBL)                   AS OIL_BBL,
       SUM(GAS_MCF)                   AS GAS_MCF,
       SUM(OIL_BBL + NGL_BBL + GAS_MCF / 6.0) AS BOE
  FROM {PC_DAILY}
 GROUP BY DATEFROMPARTS(YEAR(PROD_DATE), MONTH(PROD_DATE), 1), ALLOCATION_STATUS
 ORDER BY PROD_MONTH DESC, ALLOCATION_STATUS;""",
            snowflake="""SELECT DATE_TRUNC('month', PROD_DATE) AS PROD_MONTH,
       ALLOCATION_STATUS,
       COUNT(*)                       AS WELL_DAYS,
       COUNT(DISTINCT MERRICK_ID)     AS WELLS,
       SUM(OIL_BBL)                   AS OIL_BBL,
       SUM(GAS_MCF)                   AS GAS_MCF,
       SUM(OIL_BBL + NGL_BBL + GAS_MCF / 6.0) AS BOE
  FROM {PC_DAILY}
 GROUP BY 1, 2
 ORDER BY 1 DESC, 2;""",
            notes=(
                "Snowflake lets you GROUP BY ordinal position (1, 2) or by the output "
                "alias. SQL Server allows neither -- the expression has to be repeated in "
                "full, which is the single most common reason a Snowflake query fails to "
                "run on the source.",
                "This particular result is worth reading: ESTIMATED rows are the last three "
                "days and can move by 10%, ALLOCATED covers about 35 days and moves a few "
                "percent, FINAL is closed.",
            ),
        ),
        Lesson(
            slug="having",
            title="HAVING -- filtering the groups, not the rows",
            level="Basic",
            system="procount",
            description=(
                "WHERE filters rows before they are grouped; HAVING filters groups after "
                "the aggregate is computed. You almost always want both: WHERE to cut the "
                "scan down, HAVING to express the actual question."),
            tsql="""SELECT MERRICK_ID,
       COUNT(*)                       AS DOWN_DAYS,
       SUM(DOWNTIME_HOURS)            AS TOTAL_DOWNTIME_HOURS,
       SUM(OIL_BBL)                   AS OIL_BBL
  FROM {PC_DAILY}
 WHERE PROD_DATE >= '2026-05-01'          -- cheap, uses the index
   AND DOWNTIME_HOURS > 0
 GROUP BY MERRICK_ID
HAVING SUM(DOWNTIME_HOURS) > 100          -- the question
 ORDER BY TOTAL_DOWNTIME_HOURS DESC;""",
            snowflake="""SELECT MERRICK_ID,
       COUNT(*)                       AS DOWN_DAYS,
       SUM(DOWNTIME_HOURS)            AS TOTAL_DOWNTIME_HOURS,
       SUM(OIL_BBL)                   AS OIL_BBL
  FROM {PC_DAILY}
 WHERE PROD_DATE >= '2026-05-01'
   AND DOWNTIME_HOURS > 0
 GROUP BY MERRICK_ID
HAVING SUM(DOWNTIME_HOURS) > 100
 ORDER BY TOTAL_DOWNTIME_HOURS DESC;""",
            notes=(
                "A predicate that could be in WHERE but is written in HAVING still gives "
                "the right answer and reads every row to get there.",
                "HAVING cannot see SELECT-list aliases in SQL Server; repeat the "
                "aggregate. Snowflake does allow the alias.",
            ),
        ),
        Lesson(
            slug="grouping-sets",
            title="ROLLUP and GROUPING SETS -- subtotals in one pass",
            level="Intermediate",
            system="procount",
            description=(
                "A report that wants well, area and company totals in one result would "
                "otherwise be three queries stapled together with UNION ALL. ROLLUP "
                "produces the whole hierarchy in a single scan, and GROUPING() tells you "
                "which rows are the subtotals."),
            tsql="""SELECT COALESCE(c.AREA, '-- ALL AREAS --')          AS AREA,
       COALESCE(CAST(c.BATTERY_NAME AS varchar(40)), '-- all batteries --') AS BATTERY,
       GROUPING(c.AREA)                                AS IS_COMPANY_TOTAL,
       COUNT(DISTINCT d.MERRICK_ID)                    AS WELLS,
       SUM(d.OIL_BBL)                                  AS OIL_BBL
  FROM {PC_DAILY} d
  JOIN {PC_COMP} c ON c.MERRICK_ID = d.MERRICK_ID
 WHERE d.PROD_DATE = '2026-06-15'
 GROUP BY ROLLUP (c.AREA, c.BATTERY_NAME)
 ORDER BY GROUPING(c.AREA), AREA, BATTERY;""",
            snowflake="""SELECT COALESCE(c.AREA, '-- ALL AREAS --')          AS AREA,
       COALESCE(c.BATTERY_NAME, '-- all batteries --')  AS BATTERY,
       GROUPING(c.AREA)                                AS IS_COMPANY_TOTAL,
       COUNT(DISTINCT d.MERRICK_ID)                    AS WELLS,
       SUM(d.OIL_BBL)                                  AS OIL_BBL
  FROM {PC_DAILY} d
  JOIN {PC_COMP} c ON c.MERRICK_ID = d.MERRICK_ID
 WHERE d.PROD_DATE = '2026-06-15'
 GROUP BY ROLLUP (c.AREA, c.BATTERY_NAME)
 ORDER BY GROUPING(c.AREA), AREA, BATTERY;""",
            notes=(
                "A subtotal row has NULL in the columns it is rolled up over. That is "
                "indistinguishable from a genuine NULL in the data, which is exactly what "
                "GROUPING() is for -- do not test for NULL and hope.",
                "GROUPING SETS gives you the same machinery with explicit control: "
                "GROUP BY GROUPING SETS ((AREA, BATTERY_NAME), (AREA), ()).",
            ),
        ),
    ),
)

# ==========================================================================
# 5 -- Joining tables
# ==========================================================================
S_JOIN = Section(
    slug="join",
    title="Joining tables",
    blurb=("The three systems share exactly one value -- API_UWI -- and it is the "
           "primary key of exactly one of them. ProCount keys on MERRICK_ID, ARIES on "
           "PROPNUM, and both carry the API as an ordinary attribute their own team "
           "maintains. Every join below is a consequence of that."),
    lessons=(
        Lesson(
            slug="inner-join",
            title="INNER JOIN -- rows that exist on both sides",
            level="Basic",
            system="procount",
            description=(
                "An inner join keeps a row only when the ON condition matches. Here that is "
                "the intended behaviour: pc_completion is operated wells only, so joining "
                "the daily volumes to it is how you attach area, battery and route to a "
                "production row."),
            tsql="""SELECT TOP 50
       d.PROD_DATE,
       d.MERRICK_ID,
       c.API_UWI,
       c.COMPLETION_NAME,
       c.AREA,
       c.BATTERY_NAME,
       d.OIL_BBL,
       d.GAS_MCF
  FROM {PC_DAILY} d
  INNER JOIN {PC_COMP} c
          ON c.MERRICK_ID = d.MERRICK_ID
 WHERE d.PROD_DATE = '2026-06-15'
 ORDER BY d.OIL_BBL DESC;""",
            snowflake="""SELECT d.PROD_DATE,
       d.MERRICK_ID,
       c.API_UWI,
       c.COMPLETION_NAME,
       c.AREA,
       c.BATTERY_NAME,
       d.OIL_BBL,
       d.GAS_MCF
  FROM {PC_DAILY} d
  INNER JOIN {PC_COMP} c
          ON c.MERRICK_ID = d.MERRICK_ID
 WHERE d.PROD_DATE = '2026-06-15'
 ORDER BY d.OIL_BBL DESC
 LIMIT 50;""",
            notes=(
                "Join through MERRICK_ID, not through well_daily_prod.API_UWI. The daily "
                "table carries the API as a convenience copy; pc_completion is where the "
                "MERRICK_ID to API mapping is actually maintained, and it is the copy that "
                "gets corrected when a well is re-keyed.",
                "There are no foreign keys anywhere in these databases. Nothing enforces "
                "that d.MERRICK_ID exists in c -- the join is the only thing checking, and "
                "an inner join checks by silently deleting the row.",
            ),
        ),
        Lesson(
            slug="left-join",
            title="LEFT JOIN -- actuals against forecast, ProCount to ARIES",
            level="Basic",
            system="procount",
            description=(
                "Take every ProCount production day and attach the ARIES forecast for the "
                "same well on the same day. The join is two-part -- the well and the date "
                "-- and the well half has to hop through ARIES's own property master, "
                "because AC_DAILY is keyed on PROPNUM and ProCount has never heard of a "
                "PROPNUM. API_UWI is the bridge. LEFT keeps every actual even when no "
                "forecast row exists, which happens for any well drilled after the budget "
                "was locked."),
            tsql="""SELECT TOP 100
       d.PROD_DATE,
       c.API_UWI,
       c.COMPLETION_NAME,
       c.AREA,
       d.OIL_BBL                                        AS ACT_OIL_BBL,
       d.GAS_MCF                                        AS ACT_GAS_MCF,
       f.GROSS_OIL_BBL                                  AS FCST_OIL_BBL,
       f.GROSS_GAS_MCF                                  AS FCST_GAS_MCF,
       d.OIL_BBL - COALESCE(f.GROSS_OIL_BBL, 0)         AS VAR_OIL_BBL,
       CASE WHEN COALESCE(f.GROSS_OIL_BBL, 0) = 0 THEN NULL
            ELSE CAST(d.OIL_BBL / f.GROSS_OIL_BBL * 100 AS decimal(8,1))
       END                                              AS PCT_OF_FORECAST,
       d.ALLOCATION_STATUS,
       f.FORECAST_CASE
  FROM {PC_DAILY} d
  INNER JOIN {PC_COMP} c                                -- MERRICK_ID -> API_UWI
          ON c.MERRICK_ID = d.MERRICK_ID
  LEFT  JOIN {AC_PROP} p                                -- API_UWI -> PROPNUM
          ON p.API_UWI = c.API_UWI
  LEFT  JOIN {AC_DAILY} f                               -- PROPNUM + date
          ON f.PROPNUM = p.PROPNUM
         AND f.D_DATE  = d.PROD_DATE
 WHERE d.PROD_DATE = '2026-06-15'
 ORDER BY VAR_OIL_BBL;""",
            snowflake="""SELECT d.PROD_DATE,
       c.API_UWI,
       c.COMPLETION_NAME,
       c.AREA,
       d.OIL_BBL                                        AS ACT_OIL_BBL,
       d.GAS_MCF                                        AS ACT_GAS_MCF,
       f.GROSS_OIL_BBL                                  AS FCST_OIL_BBL,
       f.GROSS_GAS_MCF                                  AS FCST_GAS_MCF,
       d.OIL_BBL - COALESCE(f.GROSS_OIL_BBL, 0)         AS VAR_OIL_BBL,
       ROUND(d.OIL_BBL / NULLIF(f.GROSS_OIL_BBL, 0) * 100, 1) AS PCT_OF_FORECAST,
       d.ALLOCATION_STATUS,
       f.FORECAST_CASE
  FROM {PC_DAILY} d
  INNER JOIN {PC_COMP} c
          ON c.MERRICK_ID = d.MERRICK_ID
  LEFT  JOIN {AC_PROP} p
          ON p.API_UWI = c.API_UWI
  LEFT  JOIN {AC_DAILY} f
          ON f.PROPNUM = p.PROPNUM
         AND f.D_DATE  = d.PROD_DATE
 WHERE d.PROD_DATE = '2026-06-15'
 ORDER BY VAR_OIL_BBL
 LIMIT 100;""",
            notes=(
                "The date has to be in the ON clause, not the WHERE clause. Move "
                "f.D_DATE = d.PROD_DATE into WHERE and the LEFT JOIN silently becomes an "
                "inner one, because NULL = anything is not true -- every unmatched row is "
                "filtered straight back out.",
                "Once anything on the right side is NULL, arithmetic with it is NULL too. "
                "COALESCE the forecast to 0 before subtracting, or the variance column goes "
                "blank exactly on the wells you most wanted to see.",
                "This query only returns days a well actually produced. It cannot show a "
                "forecast with no actual -- a PUD that never came online. For that you need "
                "the full outer join, or the union-of-both-spines pattern in the join "
                "recipes on the Data dictionary page.",
                "On SQL Server these are three separate databases, so the names are "
                "three-part. In Snowflake after landing they are three databases in one "
                "account and the syntax is identical -- which is the point of the exercise.",
            ),
        ),
        Lesson(
            slug="right-join",
            title="RIGHT JOIN -- the same thing, backwards",
            level="Basic",
            system="aries",
            description=(
                "A RIGHT JOIN keeps every row of the second table. It is a LEFT JOIN with "
                "the tables written in the other order, and that is very nearly all there "
                "is to say about it: teams standardise on LEFT because a chain of three "
                "joins that switches direction halfway is genuinely hard to reason about."),
            tsql="""-- 'Every forecast row, with the actual if there is one.'
SELECT TOP 100
       f.D_DATE,
       f.PROPNUM,
       f.API_UWI,
       f.GROSS_OIL_BBL                  AS FCST_OIL_BBL,
       d.OIL_BBL                        AS ACT_OIL_BBL
  FROM {PC_DAILY} d
 RIGHT JOIN {AC_DAILY} f
         ON f.API_UWI = d.API_UWI
        AND f.D_DATE  = d.PROD_DATE
 WHERE f.D_DATE = '2026-06-15'
 ORDER BY d.OIL_BBL;""",
            snowflake="""SELECT f.D_DATE,
       f.PROPNUM,
       f.API_UWI,
       f.GROSS_OIL_BBL                  AS FCST_OIL_BBL,
       d.OIL_BBL                        AS ACT_OIL_BBL
  FROM {PC_DAILY} d
 RIGHT JOIN {AC_DAILY} f
         ON f.API_UWI = d.API_UWI
        AND f.D_DATE  = d.PROD_DATE
 WHERE f.D_DATE = '2026-06-15'
 ORDER BY d.OIL_BBL
 LIMIT 100;""",
            notes=(
                "Rows where ACT_OIL_BBL is NULL are forecast days with no production at "
                "all -- a well that has not come online, or one whose ProCount row has not "
                "landed yet.",
                "This shortcut joins on the API carried on both daily tables rather than "
                "going through the two masters. It is quicker to write and it is the "
                "version that breaks first when a well is re-keyed.",
            ),
        ),
        Lesson(
            slug="full-outer-join",
            title="FULL OUTER JOIN -- everything from both sides",
            level="Intermediate",
            system="procount",
            description=(
                "Keeps unmatched rows from both tables, filling the other side with NULL. "
                "This is the reconciliation join: it is the only one that can show you an "
                "actual with no forecast and a forecast with no actual in a single result."),
            tsql="""SELECT TOP 200
       COALESCE(d.API_UWI, f.API_UWI)                   AS API_UWI,
       COALESCE(d.PROD_DATE, f.D_DATE)                  AS THE_DATE,
       d.OIL_BBL                                        AS ACT_OIL_BBL,
       f.GROSS_OIL_BBL                                  AS FCST_OIL_BBL,
       CASE WHEN f.PROPNUM IS NULL THEN 'ACTUAL ONLY -- no forecast'
            WHEN d.MERRICK_ID IS NULL THEN 'FORECAST ONLY -- no actual'
            ELSE 'BOTH'
       END                                              AS MATCH_STATE
  FROM {PC_DAILY} d
  FULL OUTER JOIN {AC_DAILY} f
          ON f.API_UWI = d.API_UWI
         AND f.D_DATE  = d.PROD_DATE
 WHERE COALESCE(d.PROD_DATE, f.D_DATE) = '2026-06-15'
   AND (d.MERRICK_ID IS NULL OR f.PROPNUM IS NULL)      -- only the mismatches
 ORDER BY MATCH_STATE, API_UWI;""",
            snowflake="""SELECT COALESCE(d.API_UWI, f.API_UWI)                   AS API_UWI,
       COALESCE(d.PROD_DATE, f.D_DATE)                  AS THE_DATE,
       d.OIL_BBL                                        AS ACT_OIL_BBL,
       f.GROSS_OIL_BBL                                  AS FCST_OIL_BBL,
       CASE WHEN f.PROPNUM IS NULL THEN 'ACTUAL ONLY -- no forecast'
            WHEN d.MERRICK_ID IS NULL THEN 'FORECAST ONLY -- no actual'
            ELSE 'BOTH'
       END                                              AS MATCH_STATE
  FROM {PC_DAILY} d
  FULL OUTER JOIN {AC_DAILY} f
          ON f.API_UWI = d.API_UWI
         AND f.D_DATE  = d.PROD_DATE
 WHERE COALESCE(d.PROD_DATE, f.D_DATE) = '2026-06-15'
   AND (d.MERRICK_ID IS NULL OR f.PROPNUM IS NULL)
 ORDER BY MATCH_STATE, API_UWI
 LIMIT 200;""",
            notes=(
                "COALESCE both key columns into one output column. Selecting d.API_UWI "
                "alone leaves the forecast-only rows with a blank key, which is the "
                "classic full-outer-join bug.",
                "Any filter on one side has to be written to tolerate NULL, which is why "
                "the date predicate here is on COALESCE(...) rather than on d.PROD_DATE.",
            ),
        ),
        Lesson(
            slug="anti-join",
            title="Anti-join -- what is missing",
            level="Intermediate",
            system="wellmaster",
            description=(
                "The most valuable join in an ingestion project, because it finds the rows "
                "that should be there and are not. Every operated well in the master should "
                "have a ProCount completion and an ARIES property; anything that does not "
                "is either a genuinely new well or a broken load."),
            tsql="""SELECT w.API_UWI,
       w.WELL_NAME,
       w.AREA,
       w.WELL_STATUS,
       w.FIRST_PROD_DATE,
       CASE WHEN c.MERRICK_ID IS NULL THEN 'missing from ProCount' ELSE '' END
     + CASE WHEN p.PROPNUM   IS NULL THEN ' missing from ARIES'   ELSE '' END AS GAP
  FROM {WM} w
  LEFT JOIN {PC_COMP} c ON c.API_UWI = w.API_UWI
  LEFT JOIN {AC_PROP} p ON p.API_UWI = w.API_UWI
 WHERE w.OPERATED_FLAG = 1
   AND (c.MERRICK_ID IS NULL OR p.PROPNUM IS NULL)
 ORDER BY w.FIRST_PROD_DATE DESC;""",
            snowflake="""SELECT w.API_UWI,
       w.WELL_NAME,
       w.AREA,
       w.WELL_STATUS,
       w.FIRST_PROD_DATE,
       IFF(c.MERRICK_ID IS NULL, 'missing from ProCount', '') ||
       IFF(p.PROPNUM   IS NULL, ' missing from ARIES',   '')  AS GAP
  FROM {WM} w
  LEFT JOIN {PC_COMP} c ON c.API_UWI = w.API_UWI
  LEFT JOIN {AC_PROP} p ON p.API_UWI = w.API_UWI
 WHERE w.OPERATED_FLAG
   AND (c.MERRICK_ID IS NULL OR p.PROPNUM IS NULL)
 ORDER BY w.FIRST_PROD_DATE DESC;""",
            notes=(
                "LEFT JOIN ... WHERE right-key IS NULL and NOT EXISTS produce the same "
                "rows. NOT EXISTS says what you mean and cannot accidentally fan out; "
                "NOT IN is the one to avoid, because a single NULL in the subquery makes "
                "it return nothing at all.",
                "Run this in the other direction too. An API that exists in ProCount but "
                "not in the well master will never appear in a left join that starts at "
                "the master, and that is the worse failure.",
                "A well missing from ProCount for a day or two after coming online is "
                "normal -- the three systems are maintained by different teams on "
                "different cycles. A week is not.",
            ),
        ),
        Lesson(
            slug="self-join",
            title="Self join -- comparing a table to itself",
            level="Intermediate",
            system="procount",
            description=(
                "Join a table to another copy of itself to put two rows side by side. Here "
                "it is yesterday against today for the same well, which is the day-over-day "
                "change every morning report opens with. (A window function does this more "
                "cheaply -- see LAG -- but the self join is the version to understand "
                "first.)"),
            tsql="""SELECT TOP 100
       t.MERRICK_ID,
       t.PROD_DATE,
       y.OIL_BBL                        AS YESTERDAY_OIL,
       t.OIL_BBL                        AS TODAY_OIL,
       t.OIL_BBL - y.OIL_BBL            AS DELTA_OIL,
       t.WELL_STATUS_CODE
  FROM {PC_DAILY} t
  JOIN {PC_DAILY} y
    ON y.MERRICK_ID = t.MERRICK_ID
   AND y.PROD_DATE  = DATEADD(day, -1, t.PROD_DATE)
 WHERE t.PROD_DATE = '2026-06-15'
 ORDER BY DELTA_OIL;""",
            snowflake="""SELECT t.MERRICK_ID,
       t.PROD_DATE,
       y.OIL_BBL                        AS YESTERDAY_OIL,
       t.OIL_BBL                        AS TODAY_OIL,
       t.OIL_BBL - y.OIL_BBL            AS DELTA_OIL,
       t.WELL_STATUS_CODE
  FROM {PC_DAILY} t
  JOIN {PC_DAILY} y
    ON y.MERRICK_ID = t.MERRICK_ID
   AND y.PROD_DATE  = DATEADD('day', -1, t.PROD_DATE)
 WHERE t.PROD_DATE = '2026-06-15'
 ORDER BY DELTA_OIL
 LIMIT 100;""",
            notes=(
                "Aliases stop being optional the moment a table appears twice. Every column "
                "reference needs one.",
                "An inner self join drops any well whose previous day is missing -- a well "
                "that came online today, or a gap in the data. LEFT JOIN if you want those "
                "wells to appear with a NULL comparison.",
            ),
        ),
        Lesson(
            slug="cross-join",
            title="CROSS JOIN -- building a complete grid",
            level="Intermediate",
            system="procount",
            description=(
                "Every row of one table against every row of another. Written by accident "
                "it is a runaway query; written on purpose it builds the dense grid that "
                "reporting needs -- every well against every date, so a day a well did not "
                "report shows as a zero rather than vanishing from the chart."),
            tsql="""WITH days AS (
    SELECT DISTINCT PROD_DATE
      FROM {PC_DAILY}
     WHERE PROD_DATE BETWEEN '2026-06-01' AND '2026-06-07'
), wells AS (
    SELECT MERRICK_ID, COMPLETION_NAME, AREA
      FROM {PC_COMP}
     WHERE ACTIVE_FLAG = 1
)
SELECT TOP 200
       w.MERRICK_ID,
       w.COMPLETION_NAME,
       dy.PROD_DATE,
       COALESCE(d.OIL_BBL, 0)                       AS OIL_BBL,
       CASE WHEN d.MERRICK_ID IS NULL THEN 'NO ROW REPORTED' ELSE 'reported' END AS REPORTING
  FROM wells w
 CROSS JOIN days dy
  LEFT JOIN {PC_DAILY} d
         ON d.MERRICK_ID = w.MERRICK_ID
        AND d.PROD_DATE  = dy.PROD_DATE
 ORDER BY REPORTING, w.MERRICK_ID, dy.PROD_DATE;""",
            snowflake="""WITH days AS (
    SELECT DISTINCT PROD_DATE
      FROM {PC_DAILY}
     WHERE PROD_DATE BETWEEN '2026-06-01' AND '2026-06-07'
), wells AS (
    SELECT MERRICK_ID, COMPLETION_NAME, AREA
      FROM {PC_COMP}
     WHERE ACTIVE_FLAG
)
SELECT w.MERRICK_ID,
       w.COMPLETION_NAME,
       dy.PROD_DATE,
       COALESCE(d.OIL_BBL, 0)                       AS OIL_BBL,
       IFF(d.MERRICK_ID IS NULL, 'NO ROW REPORTED', 'reported') AS REPORTING
  FROM wells w
 CROSS JOIN days dy
  LEFT JOIN {PC_DAILY} d
         ON d.MERRICK_ID = w.MERRICK_ID
        AND d.PROD_DATE  = dy.PROD_DATE
 ORDER BY REPORTING, w.MERRICK_ID, dy.PROD_DATE
 LIMIT 200;""",
            notes=(
                "603 wells x 7 days is 4,221 rows. 603 wells x 365 days is 220,000 -- the "
                "size of AC_DAILY, which is exactly this grid, materialised. Always bound "
                "the date side.",
                "A missing row and a zero row are different facts. This pattern is how you "
                "tell them apart: a zero is a well that reported nothing produced, a "
                "missing row is a well that did not report.",
            ),
        ),
        Lesson(
            slug="three-system-join",
            title="Joining all three systems at once",
            level="Intermediate",
            system="wellmaster",
            description=(
                "The shape the whole estate exists to support: well header from the master, "
                "actuals from ProCount, forecast from ARIES, one row per well per day. "
                "Start from the well master because it is the only complete list of wells, "
                "and keep every join LEFT."),
            tsql="""SELECT TOP 200
       w.API_UWI,
       w.WELL_NAME                                  AS WM_WELL_NAME,
       p.WELL_NAME                                  AS ARIES_WELL_NAME,
       c.COMPLETION_NAME                            AS PROCOUNT_NAME,
       w.AREA                                       AS WM_AREA,
       p.AREA                                       AS ARIES_AREA,
       w.WELL_STATUS                                AS WM_STATUS,
       p.WELL_STATUS                                AS ARIES_STATUS,
       w.NET_REVENUE_INTEREST,
       d.PROD_DATE,
       d.OIL_BBL                                    AS ACT_OIL_BBL,
       f.GROSS_OIL_BBL                              AS FCST_OIL_BBL,
       CASE WHEN w.WELL_NAME <> p.WELL_NAME THEN 'name disagrees' ELSE '' END AS DQ_FLAG
  FROM {WM} w
  LEFT JOIN {PC_COMP}  c ON c.API_UWI = w.API_UWI
  LEFT JOIN {PC_DAILY} d ON d.MERRICK_ID = c.MERRICK_ID
                        AND d.PROD_DATE  = '2026-06-15'
  LEFT JOIN {AC_PROP}  p ON p.API_UWI = w.API_UWI
  LEFT JOIN {AC_DAILY} f ON f.PROPNUM = p.PROPNUM
                        AND f.D_DATE  = '2026-06-15'
 WHERE w.OPERATED_FLAG = 1
 ORDER BY w.AREA, w.WELL_NAME;""",
            snowflake="""SELECT w.API_UWI,
       w.WELL_NAME                                  AS WM_WELL_NAME,
       p.WELL_NAME                                  AS ARIES_WELL_NAME,
       c.COMPLETION_NAME                            AS PROCOUNT_NAME,
       w.AREA                                       AS WM_AREA,
       p.AREA                                       AS ARIES_AREA,
       w.WELL_STATUS                                AS WM_STATUS,
       p.WELL_STATUS                                AS ARIES_STATUS,
       w.NET_REVENUE_INTEREST,
       d.PROD_DATE,
       d.OIL_BBL                                    AS ACT_OIL_BBL,
       f.GROSS_OIL_BBL                              AS FCST_OIL_BBL,
       IFF(w.WELL_NAME <> p.WELL_NAME, 'name disagrees', '') AS DQ_FLAG
  FROM {WM} w
  LEFT JOIN {PC_COMP}  c ON c.API_UWI = w.API_UWI
  LEFT JOIN {PC_DAILY} d ON d.MERRICK_ID = c.MERRICK_ID
                        AND d.PROD_DATE  = '2026-06-15'
  LEFT JOIN {AC_PROP}  p ON p.API_UWI = w.API_UWI
  LEFT JOIN {AC_DAILY} f ON f.PROPNUM = p.PROPNUM
                        AND f.D_DATE  = '2026-06-15'
 WHERE w.OPERATED_FLAG
 ORDER BY w.AREA, w.WELL_NAME
 LIMIT 200;""",
            notes=(
                "Filtering the daily tables in the ON clause rather than in WHERE is what "
                "keeps this a one-row-per-well result. In WHERE it would inner-join and "
                "drop every well that did not produce that day.",
                "WELL_NAME, AREA and WELL_STATUS are allowed to disagree between the three "
                "systems, because in real life they do. Do not conform them during "
                "ingestion -- land all three and let the DQ_FLAG column argue about it.",
                "Swap the two date literals for a parameter and this is the delivery "
                "query. Leave it filtered to a single day while you are learning: without "
                "the filter it is 603 wells x 365 days across three databases.",
            ),
        ),
    ),
)

# ==========================================================================
# 6 -- Set operators
# ==========================================================================
S_SET = Section(
    slug="set",
    title="Set operators -- UNION, INTERSECT, EXCEPT",
    blurb=("Joins combine columns; set operators combine rows. Both sides need the "
           "same number of columns in the same order with compatible types -- names "
           "come from the first branch and everything else is matched by position."),
    lessons=(
        Lesson(
            slug="union-all",
            title="UNION ALL vs UNION -- stacking rows",
            level="Basic",
            system="procount",
            description=(
                "UNION ALL concatenates. UNION concatenates and then removes duplicate rows, "
                "which costs a sort over the whole result. Use UNION ALL unless you actively "
                "want the de-duplication, and here we actively do not: an actual and a "
                "forecast for the same well-day are two different facts."),
            tsql="""SELECT TOP 200 * FROM (
    SELECT 'ACTUAL'   AS SOURCE_SYSTEM, d.API_UWI, d.PROD_DATE AS THE_DATE,
           d.OIL_BBL  AS OIL_BBL, d.GAS_MCF AS GAS_MCF
      FROM {PC_DAILY} d
     WHERE d.PROD_DATE = '2026-06-15'
    UNION ALL
    SELECT 'FORECAST', f.API_UWI, f.D_DATE,
           f.GROSS_OIL_BBL, f.GROSS_GAS_MCF
      FROM {AC_DAILY} f
     WHERE f.D_DATE = '2026-06-15'
) stacked
ORDER BY API_UWI, SOURCE_SYSTEM;""",
            snowflake="""SELECT 'ACTUAL'   AS SOURCE_SYSTEM, d.API_UWI, d.PROD_DATE AS THE_DATE,
       d.OIL_BBL  AS OIL_BBL, d.GAS_MCF AS GAS_MCF
  FROM {PC_DAILY} d
 WHERE d.PROD_DATE = '2026-06-15'
UNION ALL
SELECT 'FORECAST', f.API_UWI, f.D_DATE,
       f.GROSS_OIL_BBL, f.GROSS_GAS_MCF
  FROM {AC_DAILY} f
 WHERE f.D_DATE = '2026-06-15'
 ORDER BY API_UWI, SOURCE_SYSTEM
 LIMIT 200;""",
            notes=(
                "Column names come from the first branch only. Alias there and nowhere "
                "else, or you will spend ten minutes wondering why the alias in branch two "
                "is being ignored.",
                "The literal SOURCE_SYSTEM column is not decoration -- once the rows are "
                "stacked it is the only thing that says where each one came from.",
                "ORDER BY belongs to the whole set operation, not to a branch. SQL Server "
                "needs the derived table here because TOP and ORDER BY inside a UNION "
                "branch would apply to that branch alone.",
            ),
        ),
        Lesson(
            slug="intersect-except",
            title="INTERSECT and EXCEPT -- overlap and difference",
            level="Intermediate",
            system="wellmaster",
            description=(
                "INTERSECT returns rows present in both sides; EXCEPT returns rows in the "
                "first and not the second. For comparing key lists across systems they are "
                "far more direct than a join, and both de-duplicate as they go."),
            tsql="""-- Operated wells the well master knows about but ProCount does not.
SELECT API_UWI FROM {WM} WHERE OPERATED_FLAG = 1
EXCEPT
SELECT API_UWI FROM {PC_COMP};""",
            snowflake="""-- Operated wells the well master knows about but ProCount does not.
SELECT API_UWI FROM {WM} WHERE OPERATED_FLAG
EXCEPT
SELECT API_UWI FROM {PC_COMP};

-- Snowflake also spells EXCEPT as MINUS.
SELECT API_UWI FROM {WM} WHERE OPERATED_FLAG
MINUS
SELECT API_UWI FROM {PC_COMP};""",
            notes=(
                "Run it in both directions. The other direction -- ProCount APIs the well "
                "master has never heard of -- is the one that indicates a real problem.",
                "INTERSECT and EXCEPT treat two NULLs as equal, unlike a join. That makes "
                "them well suited to comparing whole rows for a full-reload diff.",
                "SELECT * on both sides of EXCEPT is a complete row-level diff between two "
                "loads of the same table -- the cheapest reconciliation test there is, as "
                "long as both sides have the same columns in the same order.",
            ),
        ),
    ),
)

# ==========================================================================
# 7 -- Types, dates and time zones
# ==========================================================================
S_TYPES = Section(
    slug="types",
    title="Types, dates and time zones",
    blurb=("Three vendor products chose their own types for the same ideas, and "
           "nothing enforces agreement between them. This section starts by looking "
           "at what the types actually are, then fixes the two ways a mismatch bites "
           "-- a join that quietly matches nothing, and a UNION that refuses to run "
           "-- and finishes with the rollup all of it was for."),
    lessons=(
        Lesson(
            slug="column-types",
            title="What type is every column? Read the catalog first",
            level="Basic",
            system="procount",
            description=(
                "Before casting anything, look at what you have. INFORMATION_SCHEMA is "
                "the standard catalog view and exists in both engines, so one query gives "
                "you every join key and date column across all three databases side by "
                "side. Read the result before writing the next lesson's join: API_UWI is "
                "varchar(14) everywhere, but ProCount keys on an int, ARIES on a char(10), "
                "and every audit column is a datetime2 sitting next to date columns."),
            tsql="""SELECT 'WELL MASTER' AS SOURCE_SYSTEM, TABLE_NAME, COLUMN_NAME, DATA_TYPE,
       COALESCE(CAST(CHARACTER_MAXIMUM_LENGTH AS varchar(12)),
                CAST(DATETIME_PRECISION AS varchar(12)), '') AS SIZE,
       IS_NULLABLE
  FROM {WM_DB}.INFORMATION_SCHEMA.COLUMNS
 WHERE COLUMN_NAME IN ('API_UWI','MERRICK_ID','PROPNUM','PROD_DATE','D_DATE',
                       'FIRST_PROD_DATE','EFFECTIVE_DATE','UPDATED_TS')
UNION ALL
SELECT 'PROCOUNT', TABLE_NAME, COLUMN_NAME, DATA_TYPE,
       COALESCE(CAST(CHARACTER_MAXIMUM_LENGTH AS varchar(12)),
                CAST(DATETIME_PRECISION AS varchar(12)), ''),
       IS_NULLABLE
  FROM {PC_DB}.INFORMATION_SCHEMA.COLUMNS
 WHERE COLUMN_NAME IN ('API_UWI','MERRICK_ID','PROPNUM','PROD_DATE','D_DATE',
                       'FIRST_PROD_DATE','EFFECTIVE_DATE','UPDATED_TS')
UNION ALL
SELECT 'ARIES', TABLE_NAME, COLUMN_NAME, DATA_TYPE,
       COALESCE(CAST(CHARACTER_MAXIMUM_LENGTH AS varchar(12)),
                CAST(DATETIME_PRECISION AS varchar(12)), ''),
       IS_NULLABLE
  FROM {AC_DB}.INFORMATION_SCHEMA.COLUMNS
 WHERE COLUMN_NAME IN ('API_UWI','MERRICK_ID','PROPNUM','PROD_DATE','D_DATE',
                       'FIRST_PROD_DATE','EFFECTIVE_DATE','UPDATED_TS')
 ORDER BY COLUMN_NAME, SOURCE_SYSTEM, TABLE_NAME;""",
            snowflake="""SELECT 'WELL MASTER' AS SOURCE_SYSTEM, TABLE_NAME, COLUMN_NAME, DATA_TYPE,
       COALESCE(CHARACTER_MAXIMUM_LENGTH::VARCHAR,
                DATETIME_PRECISION::VARCHAR, '')             AS SIZE,
       IS_NULLABLE
  FROM {WM_DB}.INFORMATION_SCHEMA.COLUMNS
 WHERE COLUMN_NAME IN ('API_UWI','MERRICK_ID','PROPNUM','PROD_DATE','D_DATE',
                       'FIRST_PROD_DATE','EFFECTIVE_DATE','UPDATED_TS')
UNION ALL
SELECT 'PROCOUNT', TABLE_NAME, COLUMN_NAME, DATA_TYPE,
       COALESCE(CHARACTER_MAXIMUM_LENGTH::VARCHAR, DATETIME_PRECISION::VARCHAR, ''),
       IS_NULLABLE
  FROM {PC_DB}.INFORMATION_SCHEMA.COLUMNS
 WHERE COLUMN_NAME IN ('API_UWI','MERRICK_ID','PROPNUM','PROD_DATE','D_DATE',
                       'FIRST_PROD_DATE','EFFECTIVE_DATE','UPDATED_TS')
UNION ALL
SELECT 'ARIES', TABLE_NAME, COLUMN_NAME, DATA_TYPE,
       COALESCE(CHARACTER_MAXIMUM_LENGTH::VARCHAR, DATETIME_PRECISION::VARCHAR, ''),
       IS_NULLABLE
  FROM {AC_DB}.INFORMATION_SCHEMA.COLUMNS
 WHERE COLUMN_NAME IN ('API_UWI','MERRICK_ID','PROPNUM','PROD_DATE','D_DATE',
                       'FIRST_PROD_DATE','EFFECTIVE_DATE','UPDATED_TS')
 ORDER BY COLUMN_NAME, SOURCE_SYSTEM, TABLE_NAME;""",
            notes=(
                "What the result tells you: MERRICK_ID is int, PROPNUM is char(10), "
                "API_UWI is varchar(14) in all three systems. Every business date -- "
                "PROD_DATE, D_DATE, FIRST_PROD_DATE, EFFECTIVE_DATE -- is a plain date. "
                "Every audit column is datetime2(3). Those two facts drive the next four "
                "lessons.",
                "char(n) is blank-padded to the full width, varchar(n) is not. These "
                "PROPNUM values happen to fill all 10 characters so nothing is padded -- "
                "but a shorter one would be, and SQL Server ignores trailing blanks in a "
                "comparison while Snowflake does not. A char column that lands in Snowflake "
                "un-TRIMmed is a join that stops matching after migration.",
                "Run this against the target as well as the source once a table has "
                "landed. Comparing the two catalogs is the cheapest way to catch a load "
                "that widened, narrowed or re-typed a column on the way in.",
            ),
        ),
        Lesson(
            slug="cast-to-join",
            title="Casting to make a join work -- date against datetime2",
            level="Intermediate",
            system="procount",
            description=(
                "The join that costs people a day. ProCount's PROD_DATE is a date; the "
                "well master's UPDATED_TS is a datetime2(3) carrying a time of day. "
                "Comparing them directly is legal SQL, runs without a warning, and matches "
                "nothing -- because '2026-09-06 01:53:39.929' is never equal to "
                "'2026-09-06 00:00:00.000'. Cast the timestamp down to a date and the same "
                "join starts matching. Run this: it returns both counts on one row so you "
                "can see the difference the cast makes."),
            tsql="""-- Same join, same data, one CAST between them.
SELECT
    (SELECT COUNT(*)
       FROM {PC_DAILY} d
       JOIN {WM} w
         ON w.API_UWI = d.API_UWI
        AND w.UPDATED_TS = d.PROD_DATE)              AS MATCHED_WITHOUT_CAST,
    (SELECT COUNT(*)
       FROM {PC_DAILY} d
       JOIN {WM} w
         ON w.API_UWI = d.API_UWI
        AND CAST(w.UPDATED_TS AS date) = d.PROD_DATE) AS MATCHED_WITH_CAST;""",
            snowflake="""SELECT
    (SELECT COUNT(*)
       FROM {PC_DAILY} d
       JOIN {WM} w
         ON w.API_UWI = d.API_UWI
        AND w.UPDATED_TS = d.PROD_DATE)              AS MATCHED_WITHOUT_CAST,
    (SELECT COUNT(*)
       FROM {PC_DAILY} d
       JOIN {WM} w
         ON w.API_UWI = d.API_UWI
        AND w.UPDATED_TS::DATE = d.PROD_DATE)        AS MATCHED_WITH_CAST;""",
            notes=(
                "Zero against several hundred. Nothing errors, nothing warns -- an "
                "outer join in the same shape would give you a table full of NULLs and a "
                "report of zeroes, which is how this reaches production.",
                "Cast the *timestamp down to a date*, never the date up to a timestamp, "
                "and never wrap the column you are filtering on if you can avoid it. "
                "CAST(w.UPDATED_TS AS date) on the join key does cost the index on "
                "UPDATED_TS -- on 2,003 well master rows that is free; on a large table, "
                "compare with a half-open range instead: "
                "w.UPDATED_TS >= d.PROD_DATE AND w.UPDATED_TS < DATEADD(day, 1, d.PROD_DATE).",
                "Snowflake's :: is the same thing as CAST and reads better in a long "
                "expression. SQL Server has no :: operator -- CAST or CONVERT only.",
                "A cast in a join predicate is a smell worth acting on. If two systems "
                "need converting on every join, conform the type once during ingestion "
                "and give the warehouse a column that already matches.",
            ),
        ),
        Lesson(
            slug="union-key-types",
            title="Casting to stack rows -- int against char(10)",
            level="Intermediate",
            system="procount",
            description=(
                "The other direction. Building one key list across the three systems means "
                "putting an int (MERRICK_ID), a char(10) (PROPNUM) and a varchar(14) "
                "(API_UWI) in the same column. Unlike the join above this one does not "
                "fail quietly -- it fails loudly, because UNION resolves to the "
                "highest-precedence type it sees, decides the column is an int, and then "
                "cannot turn '06FD03F0DD' into one."),
            tsql="""-- Without the CASTs this does not run at all:
--   Conversion failed when converting the varchar value '06FD03F0DD' to data type int.
-- UNION picked int -- the higher-precedence type -- and tried to force PROPNUM into it.
SELECT TOP 200 * FROM (
    SELECT 'PROCOUNT'    AS SOURCE_SYSTEM,
           CAST(MERRICK_ID AS varchar(20)) AS SOURCE_KEY,
           API_UWI
      FROM {PC_COMP}
    UNION ALL
    SELECT 'ARIES',
           CAST(PROPNUM AS varchar(20)),
           API_UWI
      FROM {AC_PROP}
    UNION ALL
    SELECT 'WELL MASTER',
           CAST(API_UWI AS varchar(20)),
           API_UWI
      FROM {WM}
     WHERE OPERATED_FLAG = 1
) crosswalk
ORDER BY API_UWI, SOURCE_SYSTEM;""",
            snowflake="""SELECT SOURCE_SYSTEM, SOURCE_KEY, API_UWI FROM (
    SELECT 'PROCOUNT'    AS SOURCE_SYSTEM,
           MERRICK_ID::VARCHAR(20) AS SOURCE_KEY,
           API_UWI
      FROM {PC_COMP}
    UNION ALL
    SELECT 'ARIES',
           TRIM(PROPNUM)::VARCHAR(20),      -- TRIM: char(10) padding is real here
           API_UWI
      FROM {AC_PROP}
    UNION ALL
    SELECT 'WELL MASTER',
           API_UWI::VARCHAR(20),
           API_UWI
      FROM {WM}
     WHERE OPERATED_FLAG
) crosswalk
ORDER BY API_UWI, SOURCE_SYSTEM
LIMIT 200;""",
            notes=(
                "Cast everything to the widest, most permissive type -- varchar -- not to "
                "the narrowest. Casting PROPNUM to int is impossible; casting MERRICK_ID "
                "to text always works. When in doubt, text wins.",
                "Never cast API_UWI to a number to 'save space'. It is a 14-digit "
                "identifier, not a quantity: the leading digits of the county code "
                "disappear and every downstream check stops working.",
                "The Snowflake version adds TRIM around PROPNUM. SQL Server ignores "
                "trailing blanks when comparing char to varchar; Snowflake does not, so a "
                "padded char column that lands untrimmed produces keys that look identical "
                "and never match.",
                "This result is the beginning of a real deliverable -- a key crosswalk "
                "with one row per system per well, which is what you build once instead of "
                "re-deriving MERRICK_ID to PROPNUM in every query.",
            ),
        ),
        Lesson(
            slug="utc-to-local",
            title="UTC to local time -- and which columns you must not convert",
            level="Intermediate",
            system="procount",
            description=(
                "Two kinds of temporal column live in these tables and they need opposite "
                "treatment. CREATED_TS and UPDATED_TS are real instants recorded in UTC by "
                "SYSUTCDATETIME(), so a Texas user reading '01:53' is reading yesterday "
                "evening. PROD_DATE and D_DATE are wall-clock production dates with no time "
                "and no timezone at all -- converting those is not a fix, it is corruption."),
            tsql="""SELECT TOP 20
       d.MERRICK_ID,
       d.PROD_DATE,                                              -- leave alone: no timezone
       d.UPDATED_TS                                    AS UPDATED_UTC,
       d.UPDATED_TS AT TIME ZONE 'UTC'
                    AT TIME ZONE 'Central Standard Time'
                                                       AS UPDATED_CENTRAL,
       CAST(d.UPDATED_TS AT TIME ZONE 'UTC'
                         AT TIME ZONE 'Central Standard Time'
            AS datetime2(3))                           AS UPDATED_CENTRAL_NAIVE,
       CAST(d.UPDATED_TS AT TIME ZONE 'UTC'
                         AT TIME ZONE 'Central Standard Time'
            AS date)                                   AS UPDATED_LOCAL_DATE
  FROM {PC_DAILY} d
 ORDER BY d.UPDATED_TS DESC;""",
            snowflake="""SELECT d.MERRICK_ID,
       d.PROD_DATE,                                              -- leave alone
       d.UPDATED_TS                                    AS UPDATED_UTC,
       CONVERT_TIMEZONE('UTC', 'America/Chicago', d.UPDATED_TS)
                                                       AS UPDATED_CENTRAL,
       CONVERT_TIMEZONE('UTC', 'America/Chicago', d.UPDATED_TS)::DATE
                                                       AS UPDATED_LOCAL_DATE
  FROM {PC_DAILY} d
 ORDER BY d.UPDATED_TS DESC
 LIMIT 20;""",
            notes=(
                "AT TIME ZONE has to be written twice and the order matters. The first "
                "call *labels* the naive value as UTC; the second *converts* it. Write it "
                "once -- UPDATED_TS AT TIME ZONE 'Central Standard Time' -- and SQL Server "
                "reinterprets the value as if it had always been Central, shifting nothing "
                "and silently giving you a wrong answer five or six hours out.",
                "SQL Server uses Windows timezone names, Snowflake uses IANA ones: "
                "'Central Standard Time' against 'America/Chicago'. Both handle daylight "
                "saving -- the Windows name says Standard but covers CDT too, so the "
                "offset in the result above is -05:00 in summer and -06:00 in winter.",
                "AT TIME ZONE returns datetimeoffset, not datetime2. Plenty of drivers and "
                "targets cannot carry that type, so cast it: to datetime2 to keep a naive "
                "local time, or to date to get the local calendar day. Deciding which "
                "calendar day a UTC instant belongs to is the whole reason this matters -- "
                "an edit at 01:53 UTC belongs to the previous business day in Texas.",
                "Store UTC, convert at the edge. Landing local time in the warehouse means "
                "one hour every autumn exists twice and is unorderable. Snowflake's "
                "TIMESTAMP_NTZ holds the UTC value with no offset attached, which is what "
                "these columns should map to; TIMESTAMP_TZ keeps the offset if you need it.",
            ),
        ),
        Lesson(
            slug="date-conform",
            title="Conforming three date columns into one DATE_KEY",
            level="Intermediate",
            system="procount",
            description=(
                "The same idea -- a production day -- is called PROD_DATE in ProCount, "
                "D_DATE in ARIES, and has to be derived from a UTC UPDATED_TS in the well "
                "master. Nothing joins until they share a name and a type. This is the "
                "step that makes a date dimension possible, and the integer YYYYMMDD key "
                "is the warehouse convention for it."),
            tsql="""SELECT TOP 200 * FROM (
    SELECT 'PROCOUNT actual'   AS SOURCE_SYSTEM,
           CAST(CONVERT(varchar(8), d.PROD_DATE, 112) AS int) AS DATE_KEY,
           d.PROD_DATE                                        AS THE_DATE,
           d.API_UWI,
           d.OIL_BBL                                          AS OIL_BBL
      FROM {PC_DAILY} d
     WHERE d.PROD_DATE >= DATEADD(day, -2, CAST(GETDATE() AS date))
    UNION ALL
    SELECT 'ARIES forecast',
           CAST(CONVERT(varchar(8), f.D_DATE, 112) AS int),
           f.D_DATE,
           f.API_UWI,
           f.GROSS_OIL_BBL
      FROM {AC_DAILY} f
     WHERE f.D_DATE >= DATEADD(day, -2, CAST(GETDATE() AS date))
    UNION ALL
    SELECT 'WELL MASTER edit',
           CAST(CONVERT(varchar(8), CAST(w.UPDATED_TS AS date), 112) AS int),
           CAST(w.UPDATED_TS AS date),                         -- datetime2 -> date
           w.API_UWI,
           NULL
      FROM {WM} w
     WHERE w.UPDATED_TS >= DATEADD(day, -2, CAST(GETDATE() AS date))
) conformed
ORDER BY DATE_KEY DESC, SOURCE_SYSTEM, API_UWI;""",
            snowflake="""SELECT * FROM (
    SELECT 'PROCOUNT actual'   AS SOURCE_SYSTEM,
           TO_NUMBER(TO_VARCHAR(d.PROD_DATE, 'YYYYMMDD'))     AS DATE_KEY,
           d.PROD_DATE                                        AS THE_DATE,
           d.API_UWI,
           d.OIL_BBL                                          AS OIL_BBL
      FROM {PC_DAILY} d
     WHERE d.PROD_DATE >= DATEADD('day', -2, CURRENT_DATE)
    UNION ALL
    SELECT 'ARIES forecast',
           TO_NUMBER(TO_VARCHAR(f.D_DATE, 'YYYYMMDD')),
           f.D_DATE, f.API_UWI, f.GROSS_OIL_BBL
      FROM {AC_DAILY} f
     WHERE f.D_DATE >= DATEADD('day', -2, CURRENT_DATE)
    UNION ALL
    SELECT 'WELL MASTER edit',
           TO_NUMBER(TO_VARCHAR(w.UPDATED_TS::DATE, 'YYYYMMDD')),
           w.UPDATED_TS::DATE, w.API_UWI, NULL
      FROM {WM} w
     WHERE w.UPDATED_TS >= DATEADD('day', -2, CURRENT_DATE)
) conformed
ORDER BY DATE_KEY DESC, SOURCE_SYSTEM, API_UWI
LIMIT 200;""",
            notes=(
                "CONVERT style 112 is the ISO basic format, YYYYMMDD, and it is the one "
                "CONVERT style worth memorising because it is culture-independent. Style "
                "23 gives YYYY-MM-DD. Anything else is locale-dependent and will read "
                "differently on a server set to a different language.",
                "An integer DATE_KEY sorts and joins like a date, takes 4 bytes, and is "
                "instantly readable. It is also not a date: you cannot do arithmetic on "
                "it, so carry THE_DATE alongside rather than instead.",
                "The well master branch converts UTC to a calendar day with a plain CAST, "
                "which uses the UTC day. If the business means the Texas day, convert the "
                "timezone first -- see the previous lesson. Pick one and write it down; "
                "the two answers differ for anything edited between 18:00 and midnight "
                "local.",
                "The NULL in the third branch takes its type from the other branches. "
                "Where every branch could be NULL, cast it explicitly -- "
                "CAST(NULL AS decimal(12,2)) -- or the column silently becomes int.",
            ),
        ),
        Lesson(
            slug="monthly-rollup",
            title="Rolling daily production up to a month",
            level="Intermediate",
            system="procount",
            description=(
                "What all the date work was for. Collapse one row per well per day into "
                "one row per month, and get the three things a monthly report always needs "
                "beyond the totals: how many days actually reported, whether the month is "
                "complete, and rates expressed per well-day rather than per calendar day."),
            tsql="""WITH daily AS (
    SELECT DATEFROMPARTS(YEAR(PROD_DATE), MONTH(PROD_DATE), 1) AS PROD_MONTH,
           PROD_DATE, MERRICK_ID, OIL_BBL, GAS_MCF, NGL_BBL, WATER_BBL,
           PRODUCING_HOURS, ALLOCATION_STATUS
      FROM {PC_DAILY}
)
SELECT PROD_MONTH,
       EOMONTH(PROD_MONTH)                              AS MONTH_END,
       DAY(EOMONTH(PROD_MONTH))                         AS DAYS_IN_MONTH,
       COUNT(DISTINCT PROD_DATE)                        AS DAYS_REPORTED,
       COUNT(DISTINCT MERRICK_ID)                       AS WELLS,
       COUNT(*)                                         AS WELL_DAYS,
       SUM(OIL_BBL)                                     AS OIL_BBL,
       SUM(GAS_MCF)                                     AS GAS_MCF,
       SUM(NGL_BBL)                                     AS NGL_BBL,
       CAST(SUM(OIL_BBL + NGL_BBL + GAS_MCF / 6.0)
            AS decimal(14,2))                           AS BOE,
       CAST(SUM(OIL_BBL) / NULLIF(COUNT(*), 0)
            AS decimal(12,2))                           AS OIL_BOPD_PER_WELL,
       CAST(100.0 * SUM(PRODUCING_HOURS)
            / NULLIF(COUNT(*) * 24.0, 0)
            AS decimal(5,2))                            AS UPTIME_PCT,
       CASE WHEN COUNT(DISTINCT PROD_DATE) < DAY(EOMONTH(PROD_MONTH))
            THEN 'PARTIAL' ELSE 'complete' END          AS MONTH_STATE,
       CASE WHEN MIN(ALLOCATION_STATUS) = 'FINAL'
             AND MAX(ALLOCATION_STATUS) = 'FINAL'
            THEN 'closed' ELSE 'STILL MOVING' END       AS BOOK_STATE
  FROM daily
 GROUP BY PROD_MONTH
 ORDER BY PROD_MONTH DESC;""",
            snowflake="""WITH daily AS (
    SELECT DATE_TRUNC('month', PROD_DATE)               AS PROD_MONTH,
           PROD_DATE, MERRICK_ID, OIL_BBL, GAS_MCF, NGL_BBL, WATER_BBL,
           PRODUCING_HOURS, ALLOCATION_STATUS
      FROM {PC_DAILY}
)
SELECT PROD_MONTH,
       LAST_DAY(PROD_MONTH)                             AS MONTH_END,
       DAY(LAST_DAY(PROD_MONTH))                        AS DAYS_IN_MONTH,
       COUNT(DISTINCT PROD_DATE)                        AS DAYS_REPORTED,
       COUNT(DISTINCT MERRICK_ID)                       AS WELLS,
       COUNT(*)                                         AS WELL_DAYS,
       SUM(OIL_BBL)                                     AS OIL_BBL,
       SUM(GAS_MCF)                                     AS GAS_MCF,
       SUM(NGL_BBL)                                     AS NGL_BBL,
       ROUND(SUM(OIL_BBL + NGL_BBL + GAS_MCF / 6.0), 2) AS BOE,
       ROUND(SUM(OIL_BBL) / NULLIF(COUNT(*), 0), 2)     AS OIL_BOPD_PER_WELL,
       ROUND(100.0 * SUM(PRODUCING_HOURS)
             / NULLIF(COUNT(*) * 24.0, 0), 2)           AS UPTIME_PCT,
       IFF(COUNT(DISTINCT PROD_DATE) < DAY(LAST_DAY(PROD_MONTH)),
           'PARTIAL', 'complete')                       AS MONTH_STATE,
       IFF(MIN(ALLOCATION_STATUS) = 'FINAL' AND MAX(ALLOCATION_STATUS) = 'FINAL',
           'closed', 'STILL MOVING')                    AS BOOK_STATE
  FROM daily
 GROUP BY PROD_MONTH
 ORDER BY PROD_MONTH DESC;""",
            notes=(
                "Divide by WELL_DAYS, not by days in the month. A well that came online on "
                "the 20th contributed 11 days, and dividing its volume by 30 understates "
                "its rate by two thirds. This is the single most common error in a monthly "
                "production report.",
                "MONTH_STATE exists because the current month is always partial and its "
                "total is always lower than last month's. Without the flag someone reads "
                "that as a production decline. BOOK_STATE is the other half: a month whose "
                "rows are not all FINAL will still be restated.",
                "SUM over decimal(12,2) stays exact in both engines. Do not cast volumes "
                "to float to 'make the division work' -- take the division to decimal "
                "instead, as above, or a month of floating point addition will not "
                "reconcile against the daily detail.",
                "The month bucket is built in the CTE so both the GROUP BY and the SELECT "
                "can name it. SQL Server cannot group by a SELECT-list alias, and repeating "
                "DATEFROMPARTS(YEAR(...), MONTH(...), 1) in four places is how a report "
                "ends up grouped one way and labelled another.",
            ),
        ),
    ),
)

# ==========================================================================
# 8 -- Subqueries and CTEs
# ==========================================================================
S_SUBQUERY = Section(
    slug="subquery",
    title="Subqueries and CTEs",
    blurb=("A query used as a value, a set, or a table. Once a transformation needs "
           "more than one step, a common table expression is how you keep it readable "
           "-- and readability is the whole difference between a pipeline someone can "
           "maintain and one they rewrite."),
    lessons=(
        Lesson(
            slug="subquery-scalar",
            title="Scalar and IN subqueries",
            level="Intermediate",
            system="procount",
            description=(
                "A subquery returning exactly one value can be used anywhere a value can. "
                "A subquery returning one column can be used with IN. Both are read from "
                "the inside out, which is why they get hard to follow past two levels."),
            tsql="""SELECT TOP 100
       d.MERRICK_ID,
       d.PROD_DATE,
       d.OIL_BBL,
       (SELECT MAX(PROD_DATE) FROM {PC_DAILY})      AS LATEST_LOADED_DAY
  FROM {PC_DAILY} d
 WHERE d.PROD_DATE = (SELECT MAX(PROD_DATE) FROM {PC_DAILY})   -- scalar
   AND d.MERRICK_ID IN (                                       -- set
        SELECT MERRICK_ID
          FROM {PC_COMP}
         WHERE AREA = 'Midland Basin Core'
           AND ACTIVE_FLAG = 1)
 ORDER BY d.OIL_BBL DESC;""",
            snowflake="""SELECT d.MERRICK_ID,
       d.PROD_DATE,
       d.OIL_BBL,
       (SELECT MAX(PROD_DATE) FROM {PC_DAILY})      AS LATEST_LOADED_DAY
  FROM {PC_DAILY} d
 WHERE d.PROD_DATE = (SELECT MAX(PROD_DATE) FROM {PC_DAILY})
   AND d.MERRICK_ID IN (
        SELECT MERRICK_ID
          FROM {PC_COMP}
         WHERE AREA = 'Midland Basin Core'
           AND ACTIVE_FLAG)
 ORDER BY d.OIL_BBL DESC
 LIMIT 100;""",
            notes=(
                "A scalar subquery that returns more than one row is a runtime error, not a "
                "warning. 'Subquery returned more than 1 value' at 3am means someone's "
                "assumption about uniqueness stopped being true.",
                "IN against a subquery is fine. NOT IN against a subquery whose column is "
                "nullable returns zero rows for the whole query -- always NOT EXISTS "
                "instead.",
            ),
        ),
        Lesson(
            slug="correlated-exists",
            title="EXISTS and the correlated subquery",
            level="Intermediate",
            system="procount",
            description=(
                "A correlated subquery references the outer row, so it is conceptually "
                "evaluated once per row. EXISTS is the useful form: it asks whether any "
                "matching row exists and stops at the first one, and unlike a join it "
                "cannot duplicate the outer row."),
            tsql="""-- Production days that had at least one downtime event coded to
-- artificial lift, without letting the downtime table fan the row out.
SELECT TOP 100
       d.MERRICK_ID,
       d.PROD_DATE,
       d.OIL_BBL,
       d.DOWNTIME_HOURS
  FROM {PC_DAILY} d
 WHERE d.PROD_DATE >= '2026-06-01'
   AND EXISTS (
        SELECT 1
          FROM {PC_DT} dt
         WHERE dt.MERRICK_ID    = d.MERRICK_ID
           AND dt.DOWNTIME_DATE = d.PROD_DATE
           AND dt.REASON_CODE   = 'ART')
 ORDER BY d.DOWNTIME_HOURS DESC;""",
            snowflake="""SELECT d.MERRICK_ID,
       d.PROD_DATE,
       d.OIL_BBL,
       d.DOWNTIME_HOURS
  FROM {PC_DAILY} d
 WHERE d.PROD_DATE >= '2026-06-01'
   AND EXISTS (
        SELECT 1
          FROM {PC_DT} dt
         WHERE dt.MERRICK_ID    = d.MERRICK_ID
           AND dt.DOWNTIME_DATE = d.PROD_DATE
           AND dt.REASON_CODE   = 'ART')
 ORDER BY d.DOWNTIME_HOURS DESC
 LIMIT 100;""",
            notes=(
                "This is the fan-out fix. pc_daily_downtime is one row per reason, so a "
                "well that lost time twice in a day has two rows -- join it directly and "
                "that day's volumes are counted twice. EXISTS filters without joining.",
                "SELECT 1 inside EXISTS is conventional; the SELECT list is never "
                "evaluated. SELECT * would behave identically.",
                "Both engines rewrite a correlated EXISTS into a semi-join, so it is not "
                "the row-by-row loop it looks like.",
            ),
        ),
        Lesson(
            slug="cte",
            title="WITH -- common table expressions",
            level="Intermediate",
            system="procount",
            description=(
                "A CTE names a subquery and puts it at the top, so the query reads in the "
                "order the work happens instead of inside out. Several CTEs can be chained, "
                "each referring to the ones before it -- which is how a genuinely "
                "multi-step transformation stays legible."),
            tsql="""WITH daily AS (
    SELECT MERRICK_ID, PROD_DATE, OIL_BBL, GAS_MCF, NGL_BBL, DOWNTIME_HOURS
      FROM {PC_DAILY}
     WHERE PROD_DATE BETWEEN '2026-06-01' AND '2026-06-30'
), per_well AS (
    SELECT MERRICK_ID,
           COUNT(*)                                     AS DAYS,
           SUM(OIL_BBL)                                 AS OIL_BBL,
           SUM(OIL_BBL + NGL_BBL + GAS_MCF / 6.0)       AS BOE,
           SUM(DOWNTIME_HOURS)                          AS DOWNTIME_HOURS
      FROM daily
     GROUP BY MERRICK_ID
)
SELECT c.AREA,
       c.COMPLETION_NAME,
       pw.DAYS,
       pw.OIL_BBL,
       pw.BOE,
       CAST(pw.BOE / NULLIF(pw.DAYS, 0) AS decimal(12,2)) AS BOE_PER_DAY,
       CAST(100.0 * pw.DOWNTIME_HOURS
            / NULLIF(pw.DAYS * 24.0, 0) AS decimal(5,2))  AS DOWNTIME_PCT
  FROM per_well pw
  JOIN {PC_COMP} c ON c.MERRICK_ID = pw.MERRICK_ID
 ORDER BY pw.BOE DESC;""",
            snowflake="""WITH daily AS (
    SELECT MERRICK_ID, PROD_DATE, OIL_BBL, GAS_MCF, NGL_BBL, DOWNTIME_HOURS
      FROM {PC_DAILY}
     WHERE PROD_DATE BETWEEN '2026-06-01' AND '2026-06-30'
), per_well AS (
    SELECT MERRICK_ID,
           COUNT(*)                                     AS DAYS,
           SUM(OIL_BBL)                                 AS OIL_BBL,
           SUM(OIL_BBL + NGL_BBL + GAS_MCF / 6.0)       AS BOE,
           SUM(DOWNTIME_HOURS)                          AS DOWNTIME_HOURS
      FROM daily
     GROUP BY MERRICK_ID
)
SELECT c.AREA,
       c.COMPLETION_NAME,
       pw.DAYS,
       pw.OIL_BBL,
       pw.BOE,
       ROUND(pw.BOE / NULLIF(pw.DAYS, 0), 2)            AS BOE_PER_DAY,
       ROUND(100.0 * pw.DOWNTIME_HOURS
             / NULLIF(pw.DAYS * 24.0, 0), 2)            AS DOWNTIME_PCT
  FROM per_well pw
  JOIN {PC_COMP} c ON c.MERRICK_ID = pw.MERRICK_ID
 ORDER BY pw.BOE DESC;""",
            notes=(
                "A CTE is a name, not a temporary table. Referencing it twice may well "
                "evaluate it twice. If the step is expensive and reused, materialise it -- "
                "a #temp table in SQL Server, a transient table or CREATE TEMPORARY TABLE "
                "in Snowflake.",
                "The syntax is identical in both engines, which makes CTEs the natural unit "
                "for logic that has to run in both places.",
            ),
        ),
        Lesson(
            slug="recursive-cte",
            title="Recursive CTE -- generating a date spine",
            level="Intermediate",
            system="procount",
            description=(
                "A CTE that refers to itself, evaluated until the recursive branch returns "
                "nothing. The everyday use is generating rows that do not exist in any "
                "table -- most often a complete calendar to left join production onto, so "
                "days with no data still appear."),
            tsql="""WITH dates AS (
    SELECT CAST('2026-06-01' AS date) AS THE_DATE          -- anchor
    UNION ALL
    SELECT DATEADD(day, 1, THE_DATE)                       -- recursive branch
      FROM dates
     WHERE THE_DATE < '2026-06-30'
)
SELECT dy.THE_DATE,
       COUNT(d.MERRICK_ID)              AS WELLS_REPORTING,
       COALESCE(SUM(d.OIL_BBL), 0)      AS OIL_BBL
  FROM dates dy
  LEFT JOIN {PC_DAILY} d ON d.PROD_DATE = dy.THE_DATE
 GROUP BY dy.THE_DATE
 ORDER BY dy.THE_DATE
OPTION (MAXRECURSION 400);""",
            snowflake="""WITH RECURSIVE dates AS (
    SELECT DATE '2026-06-01' AS THE_DATE
    UNION ALL
    SELECT DATEADD('day', 1, THE_DATE)
      FROM dates
     WHERE THE_DATE < '2026-06-30'
)
SELECT dy.THE_DATE,
       COUNT(d.MERRICK_ID)              AS WELLS_REPORTING,
       COALESCE(SUM(d.OIL_BBL), 0)      AS OIL_BBL
  FROM dates dy
  LEFT JOIN {PC_DAILY} d ON d.PROD_DATE = dy.THE_DATE
 GROUP BY dy.THE_DATE
 ORDER BY dy.THE_DATE;""",
            notes=(
                "SQL Server stops at 100 recursions unless you add OPTION (MAXRECURSION n); "
                "a year of days needs it. Snowflake requires the RECURSIVE keyword and SQL "
                "Server forbids it -- one of the few places the WITH syntax differs.",
                "Snowflake also has GENERATOR(ROWCOUNT => n), which is faster and clearer "
                "for a spine. Recursion is the portable version.",
                "COUNT(d.MERRICK_ID) rather than COUNT(*): after a LEFT JOIN, COUNT(*) "
                "counts the unmatched row too and every empty day reports 1.",
            ),
        ),
    ),
)

# ==========================================================================
# 9 -- Window functions
# ==========================================================================
S_WINDOW = Section(
    slug="window",
    title="Window functions",
    blurb=("An aggregate that does not collapse the rows. The row keeps its detail and "
           "gains a number computed over a window of its neighbours -- which is how you "
           "get rankings, running totals, day-over-day deltas and de-duplication "
           "without a self join. The syntax is identical in both engines."),
    lessons=(
        Lesson(
            slug="row-number",
            title="ROW_NUMBER -- de-duplicating to one row per key",
            level="Intermediate",
            system="aries",
            description=(
                "Number the rows within each partition, then keep number 1. This is the "
                "standard de-duplication pattern and the standard 'latest record per key' "
                "pattern -- and after a CDC or Change Tracking load, where one key can "
                "arrive several times in one batch, it is not optional."),
            tsql="""WITH ranked AS (
    SELECT PROPNUM,
           API_UWI,
           WELL_NAME,
           SCENARIO,
           RESERVE_CAT,
           EFFECTIVE_DATE,
           UPDATED_TS,
           ROW_NUMBER() OVER (PARTITION BY API_UWI
                              ORDER BY UPDATED_TS DESC, PROPNUM DESC) AS RN
      FROM {AC_PROP}
)
SELECT PROPNUM, API_UWI, WELL_NAME, SCENARIO, RESERVE_CAT, EFFECTIVE_DATE, UPDATED_TS
  FROM ranked
 WHERE RN = 1
 ORDER BY UPDATED_TS DESC;""",
            snowflake="""SELECT PROPNUM, API_UWI, WELL_NAME, SCENARIO, RESERVE_CAT,
       EFFECTIVE_DATE, UPDATED_TS
  FROM {AC_PROP}
 QUALIFY ROW_NUMBER() OVER (PARTITION BY API_UWI
                            ORDER BY UPDATED_TS DESC, PROPNUM DESC) = 1
 ORDER BY UPDATED_TS DESC;""",
            notes=(
                "QUALIFY is Snowflake's filter on a window function -- what HAVING is to "
                "GROUP BY. SQL Server has no equivalent, so the CTE-plus-WHERE form is the "
                "portable one.",
                "Always add a tiebreaker to the ORDER BY. UPDATED_TS alone is not unique "
                "and 'the latest row' then changes between runs, which produces a pipeline "
                "that is non-deterministic in a way nobody can reproduce.",
                "A window function cannot go in a WHERE clause in either engine -- WHERE is "
                "evaluated before the window is computed. That is why the CTE exists.",
            ),
        ),
        Lesson(
            slug="rank",
            title="RANK and DENSE_RANK -- top N per group",
            level="Intermediate",
            system="procount",
            description=(
                "The three ranking functions differ only in how they treat ties. "
                "ROW_NUMBER breaks them arbitrarily, RANK gives ties the same number and "
                "then skips (1,1,3), DENSE_RANK gives ties the same number and does not "
                "skip (1,1,2). For a 'top 5 wells per area' report the choice is a business "
                "decision, not a technical one."),
            tsql="""WITH ranked AS (
    SELECT c.AREA,
           c.COMPLETION_NAME,
           d.MERRICK_ID,
           d.OIL_BBL,
           ROW_NUMBER() OVER (PARTITION BY c.AREA ORDER BY d.OIL_BBL DESC) AS RN,
           RANK()       OVER (PARTITION BY c.AREA ORDER BY d.OIL_BBL DESC) AS RNK,
           DENSE_RANK() OVER (PARTITION BY c.AREA ORDER BY d.OIL_BBL DESC) AS DRNK
      FROM {PC_DAILY} d
      JOIN {PC_COMP}  c ON c.MERRICK_ID = d.MERRICK_ID
     WHERE d.PROD_DATE = '2026-06-15'
)
SELECT AREA, COMPLETION_NAME, MERRICK_ID, OIL_BBL, RN, RNK, DRNK
  FROM ranked
 WHERE RN <= 5
 ORDER BY AREA, RN;""",
            snowflake="""SELECT c.AREA,
       c.COMPLETION_NAME,
       d.MERRICK_ID,
       d.OIL_BBL,
       ROW_NUMBER() OVER (PARTITION BY c.AREA ORDER BY d.OIL_BBL DESC) AS RN,
       RANK()       OVER (PARTITION BY c.AREA ORDER BY d.OIL_BBL DESC) AS RNK,
       DENSE_RANK() OVER (PARTITION BY c.AREA ORDER BY d.OIL_BBL DESC) AS DRNK
  FROM {PC_DAILY} d
  JOIN {PC_COMP}  c ON c.MERRICK_ID = d.MERRICK_ID
 WHERE d.PROD_DATE = '2026-06-15'
QUALIFY RN <= 5
 ORDER BY AREA, RN;""",
            notes=(
                "PARTITION BY is the window's GROUP BY; leave it off and the window is the "
                "whole result set.",
                "NTILE(4) OVER (ORDER BY OIL_BBL DESC) buckets rows into quartiles the same "
                "way, which is how a well ranking becomes a first-quartile / "
                "fourth-quartile report.",
            ),
        ),
        Lesson(
            slug="lag-lead",
            title="LAG and LEAD -- comparing to the previous row",
            level="Intermediate",
            system="procount",
            description=(
                "LAG reaches back to an earlier row in the window, LEAD forward. This "
                "replaces the self join entirely: one pass, no join, and wells with no "
                "previous day get NULL rather than disappearing. Day-over-day decline and "
                "restatement detection are both this."),
            tsql="""SELECT MERRICK_ID,
       PROD_DATE,
       OIL_BBL,
       LAG(OIL_BBL)  OVER (PARTITION BY MERRICK_ID ORDER BY PROD_DATE) AS PREV_OIL,
       OIL_BBL - LAG(OIL_BBL) OVER (PARTITION BY MERRICK_ID ORDER BY PROD_DATE)
                                                                       AS DELTA_OIL,
       LEAD(OIL_BBL) OVER (PARTITION BY MERRICK_ID ORDER BY PROD_DATE) AS NEXT_OIL,
       FIRST_VALUE(OIL_BBL) OVER (PARTITION BY MERRICK_ID ORDER BY PROD_DATE) AS IP_OIL,
       WELL_STATUS_CODE
  FROM {PC_DAILY}
 WHERE MERRICK_ID = (SELECT MIN(MERRICK_ID) FROM {PC_COMP})
 ORDER BY PROD_DATE;""",
            snowflake="""SELECT MERRICK_ID,
       PROD_DATE,
       OIL_BBL,
       LAG(OIL_BBL)  OVER (PARTITION BY MERRICK_ID ORDER BY PROD_DATE) AS PREV_OIL,
       OIL_BBL - LAG(OIL_BBL) OVER (PARTITION BY MERRICK_ID ORDER BY PROD_DATE)
                                                                       AS DELTA_OIL,
       LEAD(OIL_BBL) OVER (PARTITION BY MERRICK_ID ORDER BY PROD_DATE) AS NEXT_OIL,
       FIRST_VALUE(OIL_BBL) OVER (PARTITION BY MERRICK_ID ORDER BY PROD_DATE) AS IP_OIL,
       WELL_STATUS_CODE
  FROM {PC_DAILY}
 WHERE MERRICK_ID = (SELECT MIN(MERRICK_ID) FROM {PC_COMP})
 ORDER BY PROD_DATE;""",
            notes=(
                "LAG(col, 2) reaches back two rows; LAG(col, 1, 0) supplies a default "
                "instead of NULL for the first row of each partition.",
                "LAG works on row position, not on date arithmetic. If a well has a missing "
                "day, 'previous row' is not 'yesterday' -- left join a date spine first if "
                "the distinction matters.",
                "This query follows one well's entire decline curve from first production. "
                "Reading it top to bottom is the clearest picture of a hyperbolic Arps "
                "decline you will get out of this data.",
            ),
        ),
        Lesson(
            slug="running-total",
            title="Running totals and moving averages -- window frames",
            level="Intermediate",
            system="procount",
            description=(
                "Adding a frame to the OVER clause changes what the aggregate sees. "
                "ROWS UNBOUNDED PRECEDING gives a running cumulative total -- cumulative "
                "production to date. A bounded frame gives a moving average, which is how "
                "a noisy daily rate becomes a readable trend."),
            tsql="""SELECT MERRICK_ID,
       PROD_DATE,
       OIL_BBL,
       SUM(OIL_BBL) OVER (PARTITION BY MERRICK_ID ORDER BY PROD_DATE
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                                                    AS CUM_OIL_BBL,
       CAST(AVG(OIL_BBL) OVER (PARTITION BY MERRICK_ID ORDER BY PROD_DATE
                               ROWS BETWEEN 29 PRECEDING AND CURRENT ROW)
            AS decimal(12,2))                       AS OIL_30D_AVG,
       COUNT(*) OVER (PARTITION BY MERRICK_ID)      AS DAYS_ON_FILE,
       CAST(100.0 * OIL_BBL
            / NULLIF(MAX(OIL_BBL) OVER (PARTITION BY MERRICK_ID), 0)
            AS decimal(6,2))                        AS PCT_OF_PEAK
  FROM {PC_DAILY}
 WHERE MERRICK_ID = (SELECT MIN(MERRICK_ID) FROM {PC_COMP})
 ORDER BY PROD_DATE;""",
            snowflake="""SELECT MERRICK_ID,
       PROD_DATE,
       OIL_BBL,
       SUM(OIL_BBL) OVER (PARTITION BY MERRICK_ID ORDER BY PROD_DATE
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                                                    AS CUM_OIL_BBL,
       ROUND(AVG(OIL_BBL) OVER (PARTITION BY MERRICK_ID ORDER BY PROD_DATE
                                ROWS BETWEEN 29 PRECEDING AND CURRENT ROW), 2)
                                                    AS OIL_30D_AVG,
       COUNT(*) OVER (PARTITION BY MERRICK_ID)      AS DAYS_ON_FILE,
       ROUND(100.0 * OIL_BBL
             / NULLIF(MAX(OIL_BBL) OVER (PARTITION BY MERRICK_ID), 0), 2)
                                                    AS PCT_OF_PEAK
  FROM {PC_DAILY}
 WHERE MERRICK_ID = (SELECT MIN(MERRICK_ID) FROM {PC_COMP})
 ORDER BY PROD_DATE;""",
            notes=(
                "An OVER clause with an ORDER BY but no ROWS frame defaults to RANGE "
                "BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW, which groups tied values "
                "together. On duplicate dates that is a different answer from the ROWS "
                "frame. Write the frame out.",
                "The last column is the cleanest one-line summary of a well's decline: "
                "today's rate as a percentage of its own peak.",
            ),
        ),
    ),
)

# ==========================================================================
# 10 -- Reshaping
# ==========================================================================
S_RESHAPE = Section(
    slug="reshape",
    title="Reshaping -- pivot, unpivot, aggregate to text",
    blurb=("Turning rows into columns and back. Pivoting is a presentation step and "
           "belongs at the very end of a pipeline; unpivoting to a long, tall shape is "
           "usually the more useful direction for a warehouse."),
    lessons=(
        Lesson(
            slug="pivot",
            title="PIVOT -- rows into columns",
            level="Intermediate",
            system="procount",
            description=(
                "One column of volumes per allocation status, one row per month. SQL Server "
                "has a PIVOT operator; Snowflake has a PIVOT clause with a different shape. "
                "Conditional aggregation -- SUM(CASE WHEN ...) -- does the same job, works "
                "identically in both, and handles cases neither PIVOT syntax can."),
            tsql="""-- Portable form: conditional aggregation. Prefer this.
SELECT DATEFROMPARTS(YEAR(PROD_DATE), MONTH(PROD_DATE), 1) AS PROD_MONTH,
       SUM(CASE WHEN ALLOCATION_STATUS = 'ESTIMATED' THEN OIL_BBL ELSE 0 END) AS ESTIMATED,
       SUM(CASE WHEN ALLOCATION_STATUS = 'ALLOCATED' THEN OIL_BBL ELSE 0 END) AS ALLOCATED,
       SUM(CASE WHEN ALLOCATION_STATUS = 'FINAL'     THEN OIL_BBL ELSE 0 END) AS FINAL,
       SUM(OIL_BBL)                                                           AS TOTAL
  FROM {PC_DAILY}
 GROUP BY DATEFROMPARTS(YEAR(PROD_DATE), MONTH(PROD_DATE), 1)
 ORDER BY PROD_MONTH DESC;""",
            snowflake="""-- Conditional aggregation, identical logic.
SELECT DATE_TRUNC('month', PROD_DATE) AS PROD_MONTH,
       SUM(IFF(ALLOCATION_STATUS = 'ESTIMATED', OIL_BBL, 0)) AS ESTIMATED,
       SUM(IFF(ALLOCATION_STATUS = 'ALLOCATED', OIL_BBL, 0)) AS ALLOCATED,
       SUM(IFF(ALLOCATION_STATUS = 'FINAL',     OIL_BBL, 0)) AS FINAL,
       SUM(OIL_BBL)                                          AS TOTAL
  FROM {PC_DAILY}
 GROUP BY 1
 ORDER BY 1 DESC;

-- Or the PIVOT clause, which needs the value list spelled out.
SELECT *
  FROM (SELECT DATE_TRUNC('month', PROD_DATE) AS PROD_MONTH,
               ALLOCATION_STATUS, OIL_BBL
          FROM {PC_DAILY})
 PIVOT (SUM(OIL_BBL) FOR ALLOCATION_STATUS
        IN ('ESTIMATED', 'ALLOCATED', 'FINAL'))
 ORDER BY PROD_MONTH DESC;""",
            notes=(
                "Both PIVOT syntaxes need the output column values written out as literals. "
                "A new ALLOCATION_STATUS value appears in the data and silently does not "
                "appear in the report -- the SUM(OIL_BBL) total column is the guard against "
                "that, because it will stop equalling the sum of the pivoted columns.",
                "SQL Server's PIVOT operator implicitly groups by every column you did not "
                "name, so it must be fed from a derived table containing only the three "
                "columns involved. That surprise is most of why conditional aggregation is "
                "the better habit.",
            ),
        ),
        Lesson(
            slug="unpivot",
            title="UNPIVOT -- columns into rows",
            level="Intermediate",
            system="procount",
            description=(
                "Four volume columns become four rows with a PRODUCT column. A tall, narrow "
                "shape is what BI tools and time-series models want, and it means a new "
                "product does not need a schema change downstream. UNION ALL is the "
                "portable way to write it."),
            tsql="""SELECT MERRICK_ID, PROD_DATE, 'OIL'   AS PRODUCT, 'BBL' AS UOM, OIL_BBL   AS VOLUME
  FROM {PC_DAILY} WHERE PROD_DATE = '2026-06-15'
UNION ALL
SELECT MERRICK_ID, PROD_DATE, 'GAS',   'MCF', GAS_MCF
  FROM {PC_DAILY} WHERE PROD_DATE = '2026-06-15'
UNION ALL
SELECT MERRICK_ID, PROD_DATE, 'NGL',   'BBL', NGL_BBL
  FROM {PC_DAILY} WHERE PROD_DATE = '2026-06-15'
UNION ALL
SELECT MERRICK_ID, PROD_DATE, 'WATER', 'BBL', WATER_BBL
  FROM {PC_DAILY} WHERE PROD_DATE = '2026-06-15'
ORDER BY MERRICK_ID, PRODUCT;""",
            snowflake="""SELECT MERRICK_ID, PROD_DATE, PRODUCT, VOLUME
  FROM (SELECT MERRICK_ID, PROD_DATE, OIL_BBL, GAS_MCF, NGL_BBL, WATER_BBL
          FROM {PC_DAILY}
         WHERE PROD_DATE = '2026-06-15')
UNPIVOT (VOLUME FOR PRODUCT IN (OIL_BBL, GAS_MCF, NGL_BBL, WATER_BBL))
 ORDER BY MERRICK_ID, PRODUCT;""",
            notes=(
                "UNPIVOT drops NULLs by default in both engines. On a NOT NULL column like "
                "these it makes no difference; on a nullable one it silently changes the "
                "row count.",
                "All the unpivoted columns land in one VOLUME column, so they must share a "
                "type -- and they lose their unit. Carry the UOM explicitly, or someone "
                "will eventually add MCF to BBL.",
                "The UNION ALL form reads the base table once per branch. For four products "
                "over 128,000 rows that is fine; for twenty, use a CTE or the native "
                "UNPIVOT.",
            ),
        ),
        Lesson(
            slug="string-agg",
            title="STRING_AGG and LISTAGG -- many rows into one cell",
            level="Intermediate",
            system="procount",
            description=(
                "Collapse a group into a single delimited string. The honest use is "
                "diagnostics -- 'which reason codes did this well hit today' -- rather than "
                "a stored column, because a comma-separated list is the opposite of a "
                "normalised warehouse."),
            tsql="""SELECT MERRICK_ID,
       DOWNTIME_DATE,
       COUNT(*)                                     AS EVENTS,
       SUM(DOWNTIME_HOURS)                          AS TOTAL_HOURS,
       STRING_AGG(REASON_CODE, ', ')
           WITHIN GROUP (ORDER BY SEQ_NO)           AS REASON_CODES
  FROM {PC_DT}
 WHERE DOWNTIME_DATE >= DATEADD(day, -60, CAST(GETDATE() AS date))
 GROUP BY MERRICK_ID, DOWNTIME_DATE
 ORDER BY EVENTS DESC, TOTAL_HOURS DESC;""",
            snowflake="""SELECT MERRICK_ID,
       DOWNTIME_DATE,
       COUNT(*)                                     AS EVENTS,
       SUM(DOWNTIME_HOURS)                          AS TOTAL_HOURS,
       LISTAGG(REASON_CODE, ', ')
           WITHIN GROUP (ORDER BY SEQ_NO)           AS REASON_CODES
  FROM {PC_DT}
 WHERE DOWNTIME_DATE >= DATEADD('day', -60, CURRENT_DATE)
 GROUP BY MERRICK_ID, DOWNTIME_DATE
 ORDER BY EVENTS DESC, TOTAL_HOURS DESC;""",
            notes=(
                "Same function, two names: STRING_AGG in SQL Server (2017 and later), "
                "LISTAGG in Snowflake. The WITHIN GROUP (ORDER BY ...) clause is spelled "
                "identically and is what makes the output stable between runs.",
                "Sort by EVENTS descending and any row above 1 is the fan-out risk made "
                "visible: a well-day that a naive join from well_daily_prod to the "
                "downtime table would count twice. Add HAVING COUNT(*) > 1 to list only "
                "those.",
                "Aggregating text hides how many rows went into it. Carry the COUNT "
                "alongside, or a truncated list looks like a complete one -- SQL Server "
                "silently caps the result at 8,000 bytes unless the input is cast to "
                "varchar(max).",
            ),
        ),
    ),
)

# ==========================================================================
# 11 -- Load patterns
# ==========================================================================
S_LOAD = Section(
    slug="load",
    title="Load patterns",
    blurb=("The transformations that exist because the data is moving rather than "
           "being reported on: extracting a delta, merging it into a target, and "
           "proving afterwards that the numbers survived the trip. The write "
           "statements here are shown for reading -- the runner on this page executes "
           "reads only."),
    lessons=(
        Lesson(
            slug="watermark-extract",
            title="The watermarked extract",
            level="Intermediate",
            system="procount",
            description=(
                "The INCREMENTAL strategy on the Change feed page, written out. Read every "
                "row whose UPDATED_TS is newer than the last successful run, and record the "
                "new high-water mark. Cheap, simple, and structurally blind to hard "
                "deletes -- which is the entire reason Change Tracking and CDC exist."),
            tsql="""-- Substitute the stored watermark for the literal. The app keeps it in
-- app_state.sqlite3, never in the source -- the same place ADF keeps a
-- pipeline variable and Fivetran keeps its cursor.
SELECT MERRICK_ID,
       PROD_DATE,
       API_UWI,
       OIL_BBL, GAS_MCF, NGL_BBL, WATER_BBL,
       ALLOCATION_STATUS,
       UPDATED_TS
  FROM {PC_DAILY}
 WHERE UPDATED_TS > '2026-06-15 00:00:00'      -- > the watermark, not >=
 ORDER BY UPDATED_TS;

-- And the new watermark, taken from the same batch:
--   SELECT MAX(UPDATED_TS) FROM {PC_DAILY} WHERE UPDATED_TS > '...';""",
            snowflake="""SELECT MERRICK_ID,
       PROD_DATE,
       API_UWI,
       OIL_BBL, GAS_MCF, NGL_BBL, WATER_BBL,
       ALLOCATION_STATUS,
       UPDATED_TS
  FROM {PC_DAILY}
 WHERE UPDATED_TS > '2026-06-15 00:00:00'::TIMESTAMP_NTZ
 ORDER BY UPDATED_TS;

-- Landed in Snowflake, a stream does the same job without a watermark column:
--   CREATE STREAM PC_DAILY_STREAM ON TABLE {PC_DAILY};
--   SELECT * FROM PC_DAILY_STREAM;   -- consumed by the next DML""",
            notes=(
                "Strictly greater than, never >=, or every run re-reads the last batch. And "
                "take the new watermark from the rows you actually read, not from "
                "SYSUTCDATETIME() -- a row committed while the query was running would "
                "otherwise be skipped forever.",
                "This query cannot see a hard delete. Nothing about the result set changes "
                "when a row disappears, which is why a watermarked load needs a periodic "
                "full reconcile.",
                "It also misses any row whose UPDATED_TS was not maintained. On these "
                "tables it always is; on a real vendor database, verify it before trusting "
                "it.",
                "ARIES is the pathological case: a single re-forecast rewrites every future "
                "row for a property at once. The load looks quiet for days and then moves "
                "60,000 rows in one run. Size for the spike.",
            ),
        ),
        Lesson(
            slug="merge-upsert",
            title="MERGE -- upserting a delta into a target",
            level="Intermediate",
            system="procount",
            runnable=False,
            description=(
                "Insert the new rows, update the changed ones, in one statement matched on "
                "the natural key. This is what the loader does with the delta the previous "
                "lesson extracted. The syntax is close to identical in the two engines; the "
                "operational advice is not."),
            tsql="""MERGE INTO WAREHOUSE.STG.WELL_DAILY_PROD AS tgt
USING (
    SELECT MERRICK_ID, PROD_DATE, API_UWI, OIL_BBL, GAS_MCF, NGL_BBL,
           WATER_BBL, ALLOCATION_STATUS, UPDATED_TS
      FROM {PC_DAILY}
     WHERE UPDATED_TS > '2026-06-15 00:00:00'
) AS src
   ON tgt.MERRICK_ID = src.MERRICK_ID
  AND tgt.PROD_DATE  = src.PROD_DATE
WHEN MATCHED AND src.UPDATED_TS > tgt.UPDATED_TS THEN
    UPDATE SET tgt.OIL_BBL           = src.OIL_BBL,
               tgt.GAS_MCF           = src.GAS_MCF,
               tgt.NGL_BBL           = src.NGL_BBL,
               tgt.WATER_BBL         = src.WATER_BBL,
               tgt.ALLOCATION_STATUS = src.ALLOCATION_STATUS,
               tgt.UPDATED_TS        = src.UPDATED_TS
WHEN NOT MATCHED THEN
    INSERT (MERRICK_ID, PROD_DATE, API_UWI, OIL_BBL, GAS_MCF, NGL_BBL,
            WATER_BBL, ALLOCATION_STATUS, UPDATED_TS)
    VALUES (src.MERRICK_ID, src.PROD_DATE, src.API_UWI, src.OIL_BBL, src.GAS_MCF,
            src.NGL_BBL, src.WATER_BBL, src.ALLOCATION_STATUS, src.UPDATED_TS);""",
            snowflake="""MERGE INTO WAREHOUSE.STG.WELL_DAILY_PROD AS tgt
USING (
    SELECT MERRICK_ID, PROD_DATE, API_UWI, OIL_BBL, GAS_MCF, NGL_BBL,
           WATER_BBL, ALLOCATION_STATUS, UPDATED_TS
      FROM RAW.PC.WELL_DAILY_PROD
     QUALIFY ROW_NUMBER() OVER (PARTITION BY MERRICK_ID, PROD_DATE
                                ORDER BY UPDATED_TS DESC) = 1   -- one row per key
) AS src
   ON tgt.MERRICK_ID = src.MERRICK_ID
  AND tgt.PROD_DATE  = src.PROD_DATE
WHEN MATCHED AND src.UPDATED_TS > tgt.UPDATED_TS THEN
    UPDATE SET OIL_BBL = src.OIL_BBL, GAS_MCF = src.GAS_MCF,
               NGL_BBL = src.NGL_BBL, WATER_BBL = src.WATER_BBL,
               ALLOCATION_STATUS = src.ALLOCATION_STATUS,
               UPDATED_TS = src.UPDATED_TS
WHEN NOT MATCHED THEN
    INSERT (MERRICK_ID, PROD_DATE, API_UWI, OIL_BBL, GAS_MCF, NGL_BBL,
            WATER_BBL, ALLOCATION_STATUS, UPDATED_TS)
    VALUES (src.MERRICK_ID, src.PROD_DATE, src.API_UWI, src.OIL_BBL, src.GAS_MCF,
            src.NGL_BBL, src.WATER_BBL, src.ALLOCATION_STATUS, src.UPDATED_TS);""",
            notes=(
                "The source must have at most one row per key or MERGE is non-deterministic "
                "-- Snowflake may pick either row, SQL Server raises an error. A CDC or CT "
                "batch routinely contains several versions of the same key, so de-duplicate "
                "with ROW_NUMBER first. That is the single most common MERGE bug.",
                "The AND src.UPDATED_TS > tgt.UPDATED_TS guard makes the load idempotent: "
                "re-running the same batch changes nothing.",
                "MERGE does not delete. If the source did a hard delete, add a "
                "WHEN NOT MATCHED BY SOURCE branch -- and prefer setting a soft-delete flag "
                "over removing the row, so a report that changes retrospectively leaves a "
                "trace.",
                "The target names here are illustrative -- there is no warehouse in this "
                "project. ADF or Fivetran lands these tables; this is the SQL it generates "
                "on your behalf.",
            ),
        ),
        Lesson(
            slug="ctas-insert",
            title="CREATE TABLE AS SELECT, INSERT ... SELECT, and views",
            level="Intermediate",
            system="procount",
            runnable=False,
            description=(
                "How a transformation stops being a query and becomes an object. CTAS "
                "materialises the result; a view stores the SQL and re-runs it every time. "
                "The choice is cost against freshness, and on these volumes it is usually a "
                "view until someone complains."),
            tsql="""-- Materialise: SQL Server spells CTAS as SELECT ... INTO.
SELECT c.AREA,
       d.PROD_DATE,
       SUM(d.OIL_BBL)                               AS OIL_BBL,
       SUM(d.GAS_MCF)                               AS GAS_MCF,
       COUNT(DISTINCT d.MERRICK_ID)                 AS WELLS
  INTO  dbo.area_daily_summary
  FROM {PC_DAILY} d
  JOIN {PC_COMP}  c ON c.MERRICK_ID = d.MERRICK_ID
 GROUP BY c.AREA, d.PROD_DATE;

-- Append later runs into the same table.
INSERT INTO dbo.area_daily_summary (AREA, PROD_DATE, OIL_BBL, GAS_MCF, WELLS)
SELECT c.AREA, d.PROD_DATE, SUM(d.OIL_BBL), SUM(d.GAS_MCF),
       COUNT(DISTINCT d.MERRICK_ID)
  FROM {PC_DAILY} d
  JOIN {PC_COMP}  c ON c.MERRICK_ID = d.MERRICK_ID
 WHERE d.PROD_DATE = '2026-06-16'
 GROUP BY c.AREA, d.PROD_DATE;

-- Or store the logic instead of the rows.
CREATE VIEW dbo.v_area_daily AS
SELECT c.AREA, d.PROD_DATE, SUM(d.OIL_BBL) AS OIL_BBL
  FROM {PC_DAILY} d
  JOIN {PC_COMP}  c ON c.MERRICK_ID = d.MERRICK_ID
 GROUP BY c.AREA, d.PROD_DATE;""",
            snowflake="""-- Materialise.
CREATE OR REPLACE TABLE ANALYTICS.MART.AREA_DAILY_SUMMARY AS
SELECT c.AREA,
       d.PROD_DATE,
       SUM(d.OIL_BBL)                               AS OIL_BBL,
       SUM(d.GAS_MCF)                               AS GAS_MCF,
       COUNT(DISTINCT d.MERRICK_ID)                 AS WELLS
  FROM {PC_DAILY} d
  JOIN {PC_COMP}  c ON c.MERRICK_ID = d.MERRICK_ID
 GROUP BY 1, 2;

-- Append.
INSERT INTO ANALYTICS.MART.AREA_DAILY_SUMMARY
SELECT c.AREA, d.PROD_DATE, SUM(d.OIL_BBL), SUM(d.GAS_MCF),
       COUNT(DISTINCT d.MERRICK_ID)
  FROM {PC_DAILY} d
  JOIN {PC_COMP}  c ON c.MERRICK_ID = d.MERRICK_ID
 WHERE d.PROD_DATE = '2026-06-16'
 GROUP BY 1, 2;

-- Store the logic.
CREATE OR REPLACE VIEW ANALYTICS.MART.V_AREA_DAILY AS
SELECT c.AREA, d.PROD_DATE, SUM(d.OIL_BBL) AS OIL_BBL
  FROM {PC_DAILY} d
  JOIN {PC_COMP}  c ON c.MERRICK_ID = d.MERRICK_ID
 GROUP BY 1, 2;""",
            notes=(
                "CREATE OR REPLACE is Snowflake's default idiom and has no SQL Server "
                "equivalent -- there you drop and recreate, or use CREATE OR ALTER VIEW.",
                "SELECT ... INTO infers the column types from the query, which means a "
                "widened source column silently changes the target's type on the next "
                "rebuild. For anything permanent, write the CREATE TABLE out.",
                "INSERT ... SELECT matches columns by position, not by name. The explicit "
                "column list on the INSERT is what stops a reordered SELECT quietly loading "
                "gas volumes into the oil column.",
            ),
        ),
        Lesson(
            slug="dq-checks",
            title="Reconciliation -- proving the transformation did not lie",
            level="Intermediate",
            system="procount",
            description=(
                "Every rule below is a fact about this data that must hold, written as a "
                "query that returns a count of violations. Zero is a pass. Run them after "
                "every load: a row count only tells you rows arrived, not that they are "
                "the right ones."),
            tsql="""SELECT 'hours do not sum to 24'                AS CHECK_NAME,
       COUNT(*)                                  AS VIOLATIONS
  FROM {PC_DAILY}
 WHERE PRODUCING_HOURS + DOWNTIME_HOURS <> 24
UNION ALL
SELECT 'net volume exceeds gross',
       COUNT(*)
  FROM {PC_DAILY}
 WHERE NET_OIL_BBL > OIL_BBL
UNION ALL
SELECT 'production before first production date',
       COUNT(*)
  FROM {PC_DAILY} d
  JOIN {PC_COMP}  c ON c.MERRICK_ID = d.MERRICK_ID
 WHERE c.FIRST_PROD_DATE IS NOT NULL
   AND d.PROD_DATE < c.FIRST_PROD_DATE
UNION ALL
SELECT 'API is not 14 characters starting 42',
       COUNT(*)
  FROM {PC_COMP}
 WHERE LEN(API_UWI) <> 14 OR API_UWI NOT LIKE '42%'
UNION ALL
SELECT 'duplicate key in daily production',
       COUNT(*)
  FROM (SELECT MERRICK_ID, PROD_DATE
          FROM {PC_DAILY}
         GROUP BY MERRICK_ID, PROD_DATE
        HAVING COUNT(*) > 1) dupes
UNION ALL
SELECT 'non-operated well set up in ProCount',
       COUNT(*)
  FROM {PC_COMP} c
  JOIN {WM} w ON w.API_UWI = c.API_UWI
 WHERE w.OPERATED_FLAG = 0;""",
            snowflake="""SELECT 'hours do not sum to 24'                AS CHECK_NAME,
       COUNT(*)                                  AS VIOLATIONS
  FROM {PC_DAILY}
 WHERE PRODUCING_HOURS + DOWNTIME_HOURS <> 24
UNION ALL
SELECT 'net volume exceeds gross', COUNT(*)
  FROM {PC_DAILY}
 WHERE NET_OIL_BBL > OIL_BBL
UNION ALL
SELECT 'production before first production date', COUNT(*)
  FROM {PC_DAILY} d
  JOIN {PC_COMP}  c ON c.MERRICK_ID = d.MERRICK_ID
 WHERE c.FIRST_PROD_DATE IS NOT NULL
   AND d.PROD_DATE < c.FIRST_PROD_DATE
UNION ALL
SELECT 'API is not 14 characters starting 42', COUNT(*)
  FROM {PC_COMP}
 WHERE LENGTH(API_UWI) <> 14 OR API_UWI NOT LIKE '42%'
UNION ALL
SELECT 'duplicate key in daily production', COUNT(*)
  FROM (SELECT MERRICK_ID, PROD_DATE
          FROM {PC_DAILY}
         GROUP BY MERRICK_ID, PROD_DATE
        HAVING COUNT(*) > 1)
UNION ALL
SELECT 'non-operated well set up in ProCount', COUNT(*)
  FROM {PC_COMP} c
  JOIN {WM} w ON w.API_UWI = c.API_UWI
 WHERE NOT w.OPERATED_FLAG;""",
            notes=(
                "Write checks that return a count, not a boolean. When one fails you want "
                "to know whether it is three rows or three hundred thousand before you "
                "decide what to do about it.",
                "The API check is stricter than it looks: characters 3-5 are the county "
                "code and must match the COUNTY value. Add that join against the county "
                "reference and it catches re-keying mistakes nothing else will.",
                "A stable row count per day in well_daily_prod is its own check. A sudden "
                "drop means the daily allocation job did not finish -- not that production "
                "stopped.",
            ),
        ),
    ),
)


SECTIONS: tuple[Section, ...] = (
    S_SELECT, S_FILTER, S_SHAPE, S_AGG, S_JOIN, S_SET, S_TYPES,
    S_SUBQUERY, S_WINDOW, S_RESHAPE, S_LOAD,
)
