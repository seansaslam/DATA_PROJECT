"""Read the deployed schemas and turn them into a data dictionary and an ERD.

Structure -- tables, columns, types, nullability, primary keys, row counts -- is
read from the live catalog every time, so the documents cannot drift from the SQL
that is actually deployed. Meaning comes from descriptions.py, which is the one
thing a catalog cannot supply.
"""
from __future__ import annotations

import html

from core import connections, registry, sqlutil

from . import descriptions

SCHEMA_BY_SYSTEM = {"wellmaster": "wm", "procount": "pc", "aries": "ac"}


# ---------------------------------------------------------------------------
# Catalog introspection
# ---------------------------------------------------------------------------
def read_catalog(system_key: str) -> dict:
    spec = registry.system(system_key)
    schema = spec.schema
    out: dict = {"system": system_key, "label": spec.label, "vendor": spec.vendor,
                 "database": spec.default_database, "schema": schema,
                 "purpose": spec.purpose, "blurb": spec.blurb,
                 "tables": {}, "error": None}
    try:
        with connections.open(system_key, autocommit=True) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT TABLE_NAME, ORDINAL_POSITION, COLUMN_NAME, DATA_TYPE,
                       CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE,
                       DATETIME_PRECISION, IS_NULLABLE, COLUMN_DEFAULT
                  FROM INFORMATION_SCHEMA.COLUMNS
                 WHERE TABLE_SCHEMA = ?
                 ORDER BY TABLE_NAME, ORDINAL_POSITION""", schema)
            for (table, pos, name, dtype, clen, prec, scale, dprec, nullable,
                 default) in cur.fetchall():
                out["tables"].setdefault(table, {"columns": [], "pk": [], "rows": None})
                out["tables"][table]["columns"].append({
                    "position": pos, "name": name,
                    "type": _format_type(dtype, clen, prec, scale, dprec),
                    "nullable": nullable == "YES",
                    "default": (default or "").strip("()") or "",
                    "description": descriptions.describe(f"{schema}.{table}", name),
                })

            cur.execute("""
                SELECT t.name, c.name, ic.key_ordinal
                  FROM sys.indexes i
                  JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
                  JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
                  JOIN sys.tables t ON t.object_id = i.object_id
                  JOIN sys.schemas s ON s.schema_id = t.schema_id
                 WHERE i.is_primary_key = 1 AND s.name = ?
                 ORDER BY t.name, ic.key_ordinal""", schema)
            for table, column, _ in cur.fetchall():
                if table in out["tables"]:
                    out["tables"][table]["pk"].append(column)

            for table, meta in out["tables"].items():
                meta["rows"] = sqlutil.row_count(cur, schema, table)
                meta["note"] = descriptions.TABLE_NOTES.get(f"{schema}.{table}", "")
                meta["grain"] = descriptions.GRAIN.get(f"{schema}.{table}", "")
                meta["qualified"] = f"{schema}.{table}"
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _format_type(dtype, clen, prec, scale, dprec) -> str:
    dtype = dtype.lower()
    if clen is not None:
        return f"{dtype}({'max' if clen == -1 else clen})"
    if dtype in ("numeric", "decimal"):
        return f"{dtype}({prec},{scale})"
    if dtype in ("datetime2", "time", "datetimeoffset") and dprec is not None:
        return f"{dtype}({dprec})"
    return dtype


def data_dictionary() -> dict:
    return {key: read_catalog(key) for key in SCHEMA_BY_SYSTEM}


def undocumented() -> list[str]:
    missing = []
    for cat in data_dictionary().values():
        for table, meta in cat["tables"].items():
            for col in meta["columns"]:
                if not col["description"]:
                    missing.append(f"{cat['schema']}.{table}.{col['name']}")
    return missing


def row_counts() -> dict[str, int]:
    out: dict[str, int] = {}
    for cat in data_dictionary().values():
        for table, meta in cat["tables"].items():
            out[f"{cat['schema']}.{table}"] = meta["rows"]
    return out


# ---------------------------------------------------------------------------
# ERD
# ---------------------------------------------------------------------------
# There are no foreign keys anywhere in this estate, and there would not be in
# the real one either -- these are three separate vendor products. So the ERD
# documents the *logical* links: which primary key value turns up in which other
# table, across which database boundary, and at what cardinality.
class Box:
    HEADER = 46
    ROW = 19
    WIDTH = 310

    def __init__(self, key, title, subtitle, x, y, cols, tone):
        self.key, self.title, self.subtitle = key, title, subtitle
        self.x, self.y, self.cols, self.tone = x, y, cols, tone

    @property
    def height(self) -> int:
        return self.HEADER + self.ROW * len(self.cols) + 12

    @property
    def right(self) -> float:
        return self.x + self.WIDTH

    @property
    def mid_y(self) -> float:
        return self.y + self.height / 2


TONES = {
    "wm": ("#1d4ed8", "#eff6ff"),
    "pc": ("#b45309", "#fffbeb"),
    "ac": ("#047857", "#ecfdf5"),
}

# The Well Master sits in the middle because that is what it is: the hub every
# other system carries a copy of, and the only place the API is the primary key.
_PANELS = [
    (20, 20, 430, 810, "PROCOUNT_DB", "schema [pc] -- Quorum / Merrick ProCount", "#b45309"),
    (470, 20, 430, 810, "WELL_MASTER_DB", "schema [wm] -- in-house master data", "#1d4ed8"),
    (920, 20, 430, 810, "ARIES_DB", "schema [ac] -- Landmark ARIES", "#047857"),
]

_BOXES = [
    ("pc.pc_completion", "pc_completion", "PK: MERRICK_ID", 55, 70, [
        ("MERRICK_ID", "PK"), ("API_UWI", "LINK"), ("COMPLETION_NAME", ""),
        ("BATTERY_ID", ""), ("ACTIVE_FLAG", ""), ("ALLOC_METHOD", ""),
        ("UPDATED_TS", "AUDIT"),
    ], "pc"),
    ("pc.well_daily_prod", "well_daily_prod", "PK: MERRICK_ID + PROD_DATE", 55, 320, [
        ("MERRICK_ID", "PK"), ("PROD_DATE", "PK"), ("API_UWI", "LINK"),
        ("OIL_BBL / GAS_MCF", ""), ("WATER_BBL / NGL_BBL", ""), ("NET_OIL_BBL ...", ""),
        ("ALLOCATION_STATUS", ""), ("UPDATED_TS", "AUDIT"),
    ], "pc"),
    ("pc.pc_daily_downtime", "pc_daily_downtime", "PK: MERRICK_ID + DATE + SEQ", 55, 600, [
        ("MERRICK_ID", "PK"), ("DOWNTIME_DATE", "PK"), ("SEQ_NO", "PK"),
        ("REASON_CODE", ""), ("DEFERRED_OIL_BBL", ""), ("UPDATED_TS", "AUDIT"),
    ], "pc"),

    ("wm.cct_well_master", "cct_well_master", "PK: API_UWI", 505, 250, [
        ("API_UWI", "PK"), ("WELL_NAME / WELL_NUMBER", ""), ("OPERATED_FLAG", ""),
        ("AREA / PAD_NAME", ""), ("CENTRAL_FACILITY", ""), ("COUNTY / BASIN", ""),
        ("WELL_STATUS / WELL_TYPE", ""), ("lat / long, surface + BH", ""),
        ("SPUD / COMPL / FIRST_PROD", ""), ("WORKING_INTEREST", ""),
        ("NET_REVENUE_INTEREST", ""), ("UPDATED_TS", "AUDIT"),
    ], "wm"),

    ("ac.AC_PROPERTY", "AC_PROPERTY", "PK: PROPNUM", 955, 70, [
        ("PROPNUM", "PK"), ("API_UWI", "LINK"), ("WELL_NAME", ""),
        ("SCENARIO / RESERVE_CAT", ""), ("TYPE_CURVE", ""),
        ("QI / DI / B  (Arps)", ""), ("UPDATED_TS", "AUDIT"),
    ], "ac"),
    ("ac.AC_DAILY", "AC_DAILY", "PK: PROPNUM + D_DATE", 955, 330, [
        ("PROPNUM", "PK"), ("D_DATE", "PK"), ("API_UWI", "LINK"),
        ("GROSS_OIL_BBL / GAS_MCF", ""), ("GROSS_WATER / NGL", ""),
        ("NET_OIL_BBL ...", ""), ("FORECAST_CASE", ""), ("UPDATED_TS", "AUDIT"),
    ], "ac"),
]

# (from, to, label, cardinality)
_EDGES = [
    ("wm.cct_well_master", "pc.pc_completion", "API_UWI", "1 : 0..1"),
    ("wm.cct_well_master", "ac.AC_PROPERTY", "API_UWI", "1 : 0..1"),
    ("pc.pc_completion", "pc.well_daily_prod", "MERRICK_ID", "1 : N"),
    ("pc.pc_completion", "pc.pc_daily_downtime", "MERRICK_ID", "1 : N"),
    ("ac.AC_PROPERTY", "ac.AC_DAILY", "PROPNUM", "1 : N"),
]

EDGE_NOTES = [
    {
        "left": "wm.cct_well_master.API_UWI",
        "right": "pc.pc_completion.API_UWI",
        "card": "1 : 0..1",
        "cross_db": True,
        "note": ("Only the 603 operated wells exist in ProCount, so 1,400 well master "
                 "rows have no match. An inner join here silently drops the "
                 "non-operated position -- use a left join and let OPERATED_FLAG "
                 "explain the nulls."),
    },
    {
        "left": "wm.cct_well_master.API_UWI",
        "right": "ac.AC_PROPERTY.API_UWI",
        "card": "1 : 0..1",
        "cross_db": True,
        "note": ("Forecasts exist for operated wells only. ARIES is also free to "
                 "disagree with the well master on WELL_NAME, AREA and WELL_STATUS: "
                 "it holds its own copy and is updated on its own cycle."),
    },
    {
        "left": "pc.pc_completion.MERRICK_ID",
        "right": "pc.well_daily_prod.MERRICK_ID",
        "card": "1 : N",
        "cross_db": False,
        "note": ("One row per completion per production day, from first production "
                 "onward. A shut-in well still gets rows, carrying explicit zeros -- "
                 "absence of a row means 'not reported', not 'produced nothing'."),
    },
    {
        "left": "pc.pc_completion.MERRICK_ID",
        "right": "pc.pc_daily_downtime.MERRICK_ID",
        "card": "1 : N",
        "cross_db": False,
        "note": ("Sparse: only days that actually lost time have rows, and a single "
                 "day can carry several reasons, which is why SEQ_NO is in the key. "
                 "Joining this to the daily table fans out unless you aggregate first."),
    },
    {
        "left": "ac.AC_PROPERTY.PROPNUM",
        "right": "ac.AC_DAILY.PROPNUM",
        "card": "1 : N",
        "cross_db": False,
        "note": ("One row per property per day for the whole of calendar 2026, "
                 "including days before first production and after a shut-in, both "
                 "carrying zeros."),
    },
]


def erd_svg(counts: dict[str, int] | None = None) -> str:
    boxes = {b[0]: Box(*b) for b in _BOXES}
    parts = [
        '<svg viewBox="0 0 1370 850" xmlns="http://www.w3.org/2000/svg" '
        'font-family="ui-sans-serif, Segoe UI, Helvetica, Arial, sans-serif" '
        'class="erd-svg" role="img" aria-label="Entity relationship diagram">',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8"/></marker></defs>',
    ]
    for x, y, w, h, title, sub, colour in _PANELS:
        parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="#fff" '
            f'stroke="{colour}" stroke-opacity=".35" stroke-dasharray="7 5"/>'
            f'<text x="{x + 18}" y="{y + 28}" font-size="15" font-weight="800" '
            f'fill="{colour}" letter-spacing=".4">{html.escape(title)}</text>'
            f'<text x="{x + 18}" y="{y + 45}" font-size="11" fill="#64748b">'
            f'{html.escape(sub)}</text>')

    for src, dst, label, card in _EDGES:
        parts.append(_edge(boxes[src], boxes[dst], label, card))
    for box in boxes.values():
        parts.append(_box(box, counts))

    parts.append('<text x="685" y="843" font-size="11" text-anchor="middle" fill="#94a3b8">'
                 'Dashed lines are logical links only. There are no foreign keys anywhere '
                 'in this estate -- three separate vendor databases, joined on primary key '
                 'values.</text>')
    parts.append('</svg>')
    return "\n".join(parts)


def _box(b: Box, counts) -> str:
    stroke, fill = TONES[b.tone]
    out = [f'<g><rect x="{b.x}" y="{b.y}" width="{b.WIDTH}" height="{b.height}" rx="9" '
           f'fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>',
           f'<path d="M {b.x} {b.y + 9} a 9 9 0 0 1 9 -9 h {b.WIDTH - 18} a 9 9 0 0 1 9 9 '
           f'v {b.HEADER - 9} h -{b.WIDTH} z" fill="{stroke}"/>',
           f'<text x="{b.x + 12}" y="{b.y + 21}" font-size="14.5" font-weight="700" '
           f'fill="#fff">{html.escape(b.title)}</text>',
           f'<text x="{b.x + 12}" y="{b.y + 38}" font-size="10.5" fill="#ffffffcc" '
           f'letter-spacing=".3">{html.escape(b.subtitle)}</text>']
    n = (counts or {}).get(b.key)
    if n is not None and n >= 0:
        out.append(f'<text x="{b.right - 12}" y="{b.y + 21}" font-size="12" '
                   f'text-anchor="end" fill="#ffffffdd">{n:,} rows</text>')
    y = b.y + b.HEADER + 17
    for name, marker in b.cols:
        weight = "700" if marker in ("PK", "LINK") else "400"
        colour = {"PK": "#0f172a", "LINK": stroke, "AUDIT": "#64748b"}.get(marker, "#334155")
        out.append(f'<text x="{b.x + 12}" y="{y}" font-size="12" font-weight="{weight}" '
                   f'fill="{colour}" font-family="ui-monospace, Cascadia Mono, Consolas, '
                   f'monospace">{html.escape(name)}</text>')
        if marker:
            out.append(f'<text x="{b.right - 12}" y="{y}" font-size="9.5" text-anchor="end" '
                       f'font-weight="700" letter-spacing=".6" fill="{colour}">{marker}</text>')
        y += b.ROW
    out.append('</g>')
    return "".join(out)


def _edge(a: Box, b: Box, label: str, card: str) -> str:
    """Route right-to-left, left-to-right or straight down, whichever the two
    boxes' positions call for."""
    if abs(a.x - b.x) < 40:                                  # stacked
        x1, y1 = a.x + a.WIDTH / 2, a.y + a.height
        x2, y2 = b.x + b.WIDTH / 2, b.y
        d = f"M {x1} {y1} C {x1} {y1 + 30}, {x2} {y2 - 30}, {x2} {y2}"
        lx, ly = (x1 + x2) / 2 + 10, (y1 + y2) / 2 + 4
        anchor = "start"
    elif b.x > a.x:                                          # left to right
        x1, y1, x2, y2 = a.right, a.mid_y, b.x, b.mid_y
        dx = max(40, (x2 - x1) / 2)
        d = f"M {x1} {y1} C {x1 + dx} {y1}, {x2 - dx} {y2}, {x2} {y2}"
        lx, ly, anchor = (x1 + x2) / 2, (y1 + y2) / 2 - 8, "middle"
    else:                                                    # right to left
        x1, y1, x2, y2 = a.x, a.mid_y, b.right, b.mid_y
        dx = max(40, (x1 - x2) / 2)
        d = f"M {x1} {y1} C {x1 - dx} {y1}, {x2 + dx} {y2}, {x2} {y2}"
        lx, ly, anchor = (x1 + x2) / 2, (y1 + y2) / 2 - 8, "middle"
    text = "  ".join(p for p in (label, card) if p)
    return (f'<path d="{d}" fill="none" stroke="#94a3b8" stroke-width="1.5" '
            f'stroke-dasharray="5 4" marker-end="url(#arrow)"/>'
            f'<text x="{lx}" y="{ly}" font-size="10.5" text-anchor="{anchor}" '
            f'fill="#475569" font-weight="700">{html.escape(text)}</text>')
