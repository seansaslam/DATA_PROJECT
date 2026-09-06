"""Synthetic but physically sane well population and production for CCT Oil & Cattle.

Everything is driven off one seed, so a reset reproduces the same 2,003 wells and
the same decline curves. The daily streams come from an Arps hyperbolic decline
with per-well noise, downtime events and shut-ins layered on, which is what makes
actual-vs-forecast variance interesting rather than uniformly zero.
"""
from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass

from core.registry import (COMPANY, FORECAST_END, NON_OPERATED_WELLS,
                           OPERATED_WELLS, PRODUCTION_START)

SEED = 20260101

PROD_START = dt.date.fromisoformat(PRODUCTION_START)
FCST_END = dt.date.fromisoformat(FORECAST_END)

# ---------------------------------------------------------------------------
# Texas geography. County codes are the real 3-digit Texas county codes that
# make up characters 3-5 of an API number; state code 42 is Texas.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class County:
    name: str
    code: str
    lat: float
    lon: float
    area: str
    basin: str


COUNTIES = [
    County("MARTIN",    "317", 32.306, -101.951, "Midland Basin North", "PERMIAN"),
    County("DAWSON",    "115", 32.743, -101.947, "Midland Basin North", "PERMIAN"),
    County("BORDEN",    "033", 32.743, -101.431, "Midland Basin North", "PERMIAN"),
    County("MIDLAND",   "329", 31.869, -102.031, "Midland Basin Core",  "PERMIAN"),
    County("ECTOR",     "135", 31.869, -102.543, "Midland Basin Core",  "PERMIAN"),
    County("UPTON",     "461", 31.368, -102.043, "Midland Basin South", "PERMIAN"),
    County("REAGAN",    "383", 31.368, -101.523, "Midland Basin South", "PERMIAN"),
    County("GLASSCOCK", "173", 31.869, -101.523, "Midland Basin South", "PERMIAN"),
    County("ANDREWS",   "003", 32.305, -102.638, "Northern Shelf",      "PERMIAN"),
    County("HOWARD",    "227", 32.306, -101.436, "Northern Shelf",      "PERMIAN"),
    County("KARNES",    "255", 28.906, -97.860,  "Eagle Ford East",     "EAGLE FORD"),
    County("DEWITT",    "123", 29.088, -97.354,  "Eagle Ford East",     "EAGLE FORD"),
    County("GONZALES",  "177", 29.455, -97.492,  "Eagle Ford East",     "EAGLE FORD"),
    County("LA SALLE",  "283", 28.345, -99.100,  "Eagle Ford West",     "EAGLE FORD"),
    County("DIMMIT",    "127", 28.422, -99.756,  "Eagle Ford West",     "EAGLE FORD"),
]

AREAS = sorted({c.area for c in COUNTIES})

FIELDS = {
    "Midland Basin North": "SPRABERRY (TREND AREA)",
    "Midland Basin Core":  "SPRABERRY (TREND AREA)",
    "Midland Basin South": "SPRABERRY (TREND AREA)",
    "Northern Shelf":      "WOLFBERRY",
    "Eagle Ford East":     "EAGLEVILLE (EAGLE FORD-1)",
    "Eagle Ford West":     "BRISCOE RANCH (EAGLE FORD)",
}

RESERVOIRS = {
    "PERMIAN":    ["WOLFCAMP A", "WOLFCAMP B", "SPRABERRY LOWER", "SPRABERRY MIDDLE",
                   "DEAN", "JO MILL"],
    "EAGLE FORD": ["EAGLE FORD LOWER", "EAGLE FORD UPPER", "AUSTIN CHALK"],
}

# The company is CCT Oil & Cattle, so the leases are ranch names.
LEASE_NAMES = [
    "CROSS TIMBERS", "BAR C RANCH", "LONGHORN FLATS", "MESQUITE DRAW", "CALDWELL RANCH",
    "SANDHILL", "TWIN WINDMILLS", "CATTLEMANS", "RED STEER", "PECOS TRAIL",
    "HEREFORD", "BRAHMAN", "SALT FORK", "WAGON WHEEL", "DOUBLE T", "CHISHOLM",
    "STOCK TANK", "PRAIRIE DOG", "ANTELOPE FLAT", "COYOTE RIDGE", "BLUE QUAIL",
    "RATTLESNAKE BUTTE", "YELLOWHOUSE", "PALO DURO", "CAPROCK", "BRUSH COUNTRY",
    "SPUR ROWEL", "BRANDING IRON", "LARIAT", "MAVERICK", "STAMPEDE", "DUSTY BOOT",
    "SORREL MARE", "WIDOW MAKER", "OLD GLORY", "TUMBLEWEED", "SAGEBRUSH",
    "COTTONWOOD CREEK", "INDIAN MOUND", "BUFFALO WALLOW",
]

OUTSIDE_OPERATORS = [
    "PIONEER NATURAL RES", "DIAMONDBACK E&P LLC", "APACHE CORPORATION",
    "CHEVRON USA INC", "OXY USA INC", "EOG RESOURCES INC", "CONOCOPHILLIPS CO",
    "DEVON ENERGY PROD", "COTERRA ENERGY INC", "MEWBOURNE OIL CO",
    "SM ENERGY COMPANY", "CALLON PETROLEUM",
]

DOWNTIME_REASONS = [
    ("ART",  "Artificial lift failure"),
    ("COMP", "Compressor down"),
    ("ELEC", "Electrical / power outage"),
    ("FL",   "Flowline repair"),
    ("HIGH", "High line pressure"),
    ("WO",   "Workover rig on location"),
    ("TEST", "Well test"),
    ("WX",   "Weather / freeze off"),
    ("SALE", "No sales / midstream curtailment"),
    ("TANK", "Tank battery at capacity"),
]

WELL_STATUS_PRODUCING = "PRODUCING"
WELL_STATUS_SHUT_IN = "SHUT-IN"


@dataclass
class Well:
    api_uwi: str
    well_name: str
    well_number: str
    operator_name: str
    operated: bool
    area: str
    pad_name: str
    central_facility: str
    field_name: str
    county: str
    state_code: str
    basin: str
    reservoir: str
    well_status: str
    well_type: str
    surface_lat: float
    surface_lon: float
    bh_lat: float
    bh_lon: float
    spud_date: dt.date
    completion_date: dt.date
    first_prod_date: dt.date
    lateral_ft: int
    total_depth_ft: int
    wi: float
    nri: float
    # -- production model (not persisted in the well master) -----------------
    merrick_id: int | None = None
    propnum: str | None = None
    qi_oil: float = 0.0          # initial oil rate, BOPD
    di_nominal: float = 0.0      # annual nominal decline
    b_factor: float = 0.0
    gor: float = 0.0             # scf/bbl
    ngl_yield: float = 0.0       # bbl per MMcf
    wor: float = 0.0             # water/oil ratio at t0
    shut_in_from: dt.date | None = None   # permanently shut in on/after this date
    prod_route: str = ""
    battery_id: int = 0
    battery_name: str = ""
    reserve_cat: str = "PDP"
    type_curve: str = ""


# ---------------------------------------------------------------------------
# Well population
# ---------------------------------------------------------------------------
def build_wells(seed: int = SEED) -> list[Well]:
    """603 operated + 1,400 non-operated wells, deterministic for a given seed."""
    rng = random.Random(seed)
    wells: list[Well] = []
    used_api: set[str] = set()
    sequence = 10000
    merrick = 100001

    total = OPERATED_WELLS + NON_OPERATED_WELLS
    for i in range(total):
        operated = i < OPERATED_WELLS
        county = rng.choice(COUNTIES)

        sequence += rng.randint(1, 4)
        api = f"42{county.code}{sequence:05d}0000"
        while api in used_api:                       # belt and braces
            sequence += 1
            api = f"42{county.code}{sequence:05d}0000"
        used_api.add(api)

        w = make_well(rng, operated=operated, api=api, county=county)
        if operated:
            w.merrick_id = merrick
            merrick += rng.randint(1, 3)
        wells.append(w)

    return wells


def make_well(rng: random.Random, *, operated: bool, api: str,
              county: County | None = None) -> Well:
    """Build one well. Shared by the bulk seeder and by the churn engine, which
    brings new wells online while the simulation is running."""
    county = county or rng.choice(COUNTIES)
    lease = rng.choice(LEASE_NAMES)

    pad_no = rng.randint(1, 140)
    unit = rng.choice(["A", "B", "C", "D"])
    well_no = f"{rng.randint(1, 12)}{unit}H"
    pad_name = f"{lease.split()[0][:6]}-PAD-{pad_no:03d}"
    battery_id = 4000 + (pad_no % 60)

    reservoir = rng.choice(RESERVOIRS[county.basin])
    gas_well = rng.random() < (0.30 if county.basin == "EAGLE FORD" else 0.18)
    well_type = "GAS" if gas_well else "OIL"

    lateral = rng.choice([4500, 5000, 7500, 8500, 9500, 10000, 11500, 12500])
    tvd = rng.randint(7200, 11800) if county.basin == "PERMIAN" else rng.randint(9000, 13500)

    # Company started producing 2026-01-01. Most wells are online day one;
    # the rest come online through the year, which is what makes an
    # incremental load see genuinely new keys.
    if rng.random() < 0.72:
        first_prod = PROD_START
    else:
        first_prod = PROD_START + dt.timedelta(days=rng.randint(1, 240))
    completion = first_prod - dt.timedelta(days=rng.randint(5, 25))
    spud = completion - dt.timedelta(days=rng.randint(35, 110))

    wi = round(rng.uniform(0.62, 1.0), 6) if operated else round(rng.uniform(0.01, 0.28), 6)
    nri = round(wi * rng.uniform(0.72, 0.80), 6)

    # ~6% of wells are shut in at some point and stay down.
    shut_in_from = None
    if rng.random() < 0.06:
        shut_in_from = first_prod + dt.timedelta(days=rng.randint(20, 200))

    status = WELL_STATUS_PRODUCING
    if shut_in_from and shut_in_from <= _TODAY:
        status = WELL_STATUS_SHUT_IN

    jitter = lambda base: round(base + rng.uniform(-0.42, 0.42), 6)  # noqa: E731
    s_lat, s_lon = jitter(county.lat), jitter(county.lon)
    # A lateral runs roughly north-south; 10,000 ft is about 0.027 degrees.
    b_lat = round(s_lat + (lateral / 364000.0) * rng.choice([1, -1]), 6)
    b_lon = round(s_lon + rng.uniform(-0.004, 0.004), 6)

    if gas_well:
        qi = round(rng.uniform(60, 220) * (lateral / 10000.0), 2)
    else:
        qi = round(rng.uniform(450, 1500) * (lateral / 10000.0), 2)
    gor, ngl_yield, wor = model_params(api, well_type)

    w = Well(
        api_uwi=api,
        well_name=f"{lease} {unit} UNIT",
        well_number=well_no,
        operator_name=COMPANY.upper() if operated else rng.choice(OUTSIDE_OPERATORS),
        operated=operated,
        area=county.area,
        pad_name=pad_name,
        central_facility=f"CTB-{battery_id - 4000:02d}",
        field_name=FIELDS[county.area],
        county=county.name,
        state_code="TX",
        basin=county.basin,
        reservoir=reservoir,
        well_status=status,
        well_type=well_type,
        surface_lat=s_lat, surface_lon=s_lon, bh_lat=b_lat, bh_lon=b_lon,
        spud_date=spud, completion_date=completion, first_prod_date=first_prod,
        lateral_ft=lateral, total_depth_ft=tvd, wi=wi, nri=nri,
        qi_oil=qi,
        di_nominal=round(rng.uniform(0.62, 0.88), 6),
        b_factor=round(rng.uniform(0.9, 1.4), 4),
        gor=gor, ngl_yield=ngl_yield, wor=wor,
        shut_in_from=shut_in_from,
        prod_route=f"RTE-{county.name[:3]}-{rng.randint(1, 9)}",
        battery_id=battery_id,
        battery_name=f"CTB-{battery_id - 4000:02d} {lease.split()[0].title()}",
        reserve_cat=rng.choices(["PDP", "PDNP", "PUD"], weights=[88, 7, 5])[0],
        type_curve=f"{county.basin.split()[0][:3]}-{reservoir.split()[0][:4]}-{lateral // 1000}K",
    )

    if operated:
        w.propnum = _propnum(rng)
    return w

def model_params(api_uwi: str, well_type: str) -> tuple[float, float, float]:
    """Gas-oil ratio, NGL yield and water-oil ratio, derived from the API itself.

    Keying these off the API rather than the seeding RNG means the churn engine
    can reconstruct any well's rate model from what the databases already store
    -- Arps constants live in ac.AC_PROPERTY, dates and interests in the well
    master -- without a hidden state file that could drift out of step.
    """
    r = random.Random(int(api_uwi))
    gor = r.uniform(6000, 18000) if well_type == "GAS" else r.uniform(900, 3200)
    return round(gor, 1), round(r.uniform(45, 130), 2), round(r.uniform(1.4, 6.5), 3)


_PROPNUM_CHARS = "0123456789ABCDEF"
_propnum_seen: set[str] = set()


def _propnum(rng: random.Random) -> str:
    while True:
        p = "".join(rng.choice(_PROPNUM_CHARS) for _ in range(10))
        if p not in _propnum_seen:
            _propnum_seen.add(p)
            return p


_TODAY = dt.date.today()


def today() -> dt.date:
    return dt.date.today()


def date_range(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


# ---------------------------------------------------------------------------
# Rate model
# ---------------------------------------------------------------------------
def arps_rate(qi: float, di: float, b: float, days_on: int) -> float:
    """Hyperbolic Arps decline. di is an annual nominal rate, t is in years."""
    if days_on < 0:
        return 0.0
    t = days_on / 365.25
    if b <= 0.001:
        import math
        return qi * math.exp(-di * t)
    denom = (1.0 + b * di * t) ** (1.0 / b)
    return qi / denom if denom else 0.0


def forecast_day(w: Well, day: dt.date) -> tuple[float, float, float, float]:
    """The clean type-curve volumes ARIES would carry: oil, gas, water, ngl."""
    if day < w.first_prod_date:
        return 0.0, 0.0, 0.0, 0.0
    if w.shut_in_from and day >= w.shut_in_from:
        return 0.0, 0.0, 0.0, 0.0
    days_on = (day - w.first_prod_date).days
    oil = arps_rate(w.qi_oil, w.di_nominal, w.b_factor, days_on)
    gas = oil * w.gor / 1000.0
    if w.well_type == "GAS":
        gas = max(gas, arps_rate(w.qi_oil * 12, w.di_nominal, w.b_factor, days_on))
    ngl = gas / 1000.0 * w.ngl_yield
    # Water cut climbs as the well matures.
    water = oil * w.wor * (1.0 + days_on / 900.0)
    return round(oil, 2), round(gas, 2), round(water, 2), round(ngl, 2)


def actual_day(w: Well, day: dt.date, rng: random.Random) -> dict:
    """What ProCount would allocate: the forecast, beaten up by real life."""
    oil, gas, water, ngl = forecast_day(w, day)

    if oil == 0.0 and gas == 0.0:
        status = WELL_STATUS_SHUT_IN if (w.shut_in_from and day >= w.shut_in_from) else "DOWN"
        return {"oil": 0.0, "gas": 0.0, "water": 0.0, "ngl": 0.0,
                "producing_hours": 0.0, "downtime_hours": 24.0,
                "status": status, "downtime": None,
                "tubing": None, "casing": None, "choke": None}

    # Operational uptime: most days are clean, some lose part of a day.
    downtime_hours, downtime = 0.0, None
    roll = rng.random()
    if roll < 0.055:
        downtime_hours = round(rng.uniform(1.0, 24.0), 2)
        code, desc = rng.choice(DOWNTIME_REASONS)
        downtime = (code, desc, downtime_hours)
    producing_hours = round(24.0 - downtime_hours, 2)
    uptime = producing_hours / 24.0

    # Measurement + allocation noise on top of the type curve.
    noise = rng.gauss(1.0, 0.11)
    noise = min(max(noise, 0.55), 1.45)

    oil_a = round(max(oil * uptime * noise, 0.0), 2)
    gas_a = round(max(gas * uptime * rng.gauss(1.0, 0.09), 0.0), 2)
    water_a = round(max(water * uptime * rng.gauss(1.0, 0.14), 0.0), 2)
    ngl_a = round(max(ngl * uptime * rng.gauss(1.0, 0.10), 0.0), 2)

    if downtime:
        downtime = (downtime[0], downtime[1], downtime[2],
                    round(max(oil - oil_a, 0.0), 2), round(max(gas - gas_a, 0.0), 2))

    return {
        "oil": oil_a, "gas": gas_a, "water": water_a, "ngl": ngl_a,
        "producing_hours": producing_hours, "downtime_hours": downtime_hours,
        "status": "PRODUCING" if producing_hours > 0 else "DOWN",
        "downtime": downtime,
        "tubing": int(rng.uniform(180, 1400)),
        "casing": int(rng.uniform(90, 900)),
        "choke": rng.choice([16, 18, 20, 22, 24, 28, 32, 40, 48, 64]),
    }


def allocation_status(day: dt.date, asof: dt.date) -> str:
    """Volumes firm up over time: the last few days are estimates, the last
    month is allocated, everything older has been closed out."""
    age = (asof - day).days
    if age <= 3:
        return "ESTIMATED"
    if age <= 35:
        return "ALLOCATED"
    return "FINAL"
