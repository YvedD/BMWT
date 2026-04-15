#!/usr/bin/env python3
"""
Refresh migration prediction data for BMWT.

Fetches a 8-day hourly weather forecast for a ~100 × 100 km raster over
Western Europe (anchored on Tarifa, Spain) and computes a migration-
favourability score for each grid point per day.

Grid points in the sea or in the United Kingdom are excluded automatically
via TimezoneFinder.

Score  0.0 = extremely unfavourable
Score  1.0 = extremely favourable

NOTE – Zeebries (sea breeze) detection is NOT included in this script.
  The sea breeze feature uses a set of hard-coded coastal locations
  (Saint-Malo to Esbjerg) and the Open-Meteo Marine API for SST data.
  It is computed live in the Streamlit app (Bird_Migration_Tool.py,
  laad_zeebries_kustdata / detecteer_zeebries_uur) with its own 30-min cache.

Run by the GitHub Actions workflow every 30 minutes.
Output: data/migration/latest.json
"""

import concurrent.futures
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

from timezonefinder import TimezoneFinder

OUTPUT_PATH = Path("data/migration/latest.json")
SCORE_WEIGHTS_PATH = Path("data/migration/score_weights.json")


def _load_score_weights() -> dict | None:
    """Load user-configurable scoring weights from JSON if available."""
    try:
        if SCORE_WEIGHTS_PATH.exists():
            with open(SCORE_WEIGHTS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


_USER_CFG = _load_score_weights()

# Grid anchor: Tarifa, Spain
ANCHOR_LAT  = 36.0
ANCHOR_LON  = -5.6
LAT_STEP    = 1.0    # degrees latitude  (~111 km)
LON_STEP    = 1.3    # degrees longitude (~100 km at lat 36°N)
LAT_MIN     = 35.0
LAT_MAX     = 56.0
LON_MIN     = -9.5
LON_MAX     = 15.3

MAX_WORKERS   = 20     # parallel HTTP workers
FORECAST_DAYS = 8     # today + 7 days ahead
FORECAST_HOURS = FORECAST_DAYS * 24   # = 192 hourly values

# Flight altitude thresholds (km/h)
VLIEGHOOGTE_GESTOPT_THRESHOLD = 50   # ≥ 7 Bf: migration suppressed
VLIEGHOOGTE_LAAG_MIN          = 29   # 5–6 Bf: birds fly low (good observability)
VLIEGHOOGTE_MIDDEL_MIN        = 12   # 3–4 Bf: mid-altitude

# Default fallback values for missing forecast variables
CAPE_DEFAULT = 0.0
BLH_DEFAULT  = 500.0

# BE/NL regional SE-wind optimum
# SE wind (135°, 3–5 Bf) is optimal for Belgium/Netherlands:
# birds are pushed from central France to the North Sea coast.
BENE_LAT_MIN        = 49.5
BENE_LAT_MAX        = 53.5
BENE_LON_MIN        = 2.0
BENE_LON_MAX        = 8.0

# Asymmetric directional falloff around ZO (135°):
#   South side (toward ZZO/Z): slower decay
#   East  side (toward OZO/O): faster decay
BENE_WIND_OPT_DIR   = 135.0   # optimal wind direction (degrees)
BENE_WIND_FALLOFF_S = 225.0   # reach 0 at this many degrees CW past ZO (= near W)
BENE_WIND_FALLOFF_E = 135.0   # reach 0 at this many degrees CCW past ZO (= near N)

# Beaufort speed thresholds (km/h) — overridden by score_weights.json if present
_ws = (_USER_CFG or {}).get("wind_snelheid_bf", {})
BENE_WIND_SPEED_1BF = float(_ws.get("bf1", 1.0))
BENE_WIND_SPEED_3BF = float(_ws.get("bf3_min", 12.0))
BENE_WIND_SPEED_3BF_MAX = float(_ws.get("bf3_max", 20.0))
BENE_WIND_SPEED_5BF = float(_ws.get("bf5_max", 38.0))
BENE_WIND_SPEED_7BF = float(_ws.get("bf7_min", 50.0))

# Wind direction correction factors — overridden by score_weights.json if present
_wc = (_USER_CFG or {}).get("wind_correctie", {})
WIND_WEST_PENALTY   = float(_wc.get("west_penalty", 0.5))
WIND_SEA_BONUS      = float(_wc.get("zee_bonus", 0.4))
WIND_NW_W_DIR_MIN   = float(_wc.get("nw_w_richting_min", 225.0))
WIND_NW_W_DIR_MAX   = float(_wc.get("nw_w_richting_max", 330.0))

WIND_DIRECTION_LABELS = (
    "N", "NNO", "NO", "ONO", "O", "OZO", "ZO", "ZZO",
    "Z", "ZZW", "ZW", "WZW", "W", "WNW", "NW", "NNW",
)
SPRING_ZERO_WIND_ALL_SPEEDS = frozenset(
    (_USER_CFG or {}).get("voorjaar_wind_nul_alle_snelheden", ["W", "NW", "WNW", "NNW", "WZW"])
)
SPRING_ZERO_WIND_STRICTLY_ABOVE_3BF = frozenset(
    (_USER_CFG or {}).get("voorjaar_wind_nul_strikt_boven_3bf", ["ZW", "N", "NNO"])
)
SPRING_MAX_WIND_STRICTLY_BELOW_3BF = frozenset(
    (_USER_CFG or {}).get("voorjaar_wind_max_strikt_onder_3bf", ["ZW", "ZZW"])
)

# Score weights — overridden by score_weights.json if present
_sg = (_USER_CFG or {}).get("score_gewichten", {})
SCORE_WEIGHT_WIND_DIRECTION = float(_sg.get("windrichting", 0.70))
SCORE_WEIGHT_TEMPERATURE    = float(_sg.get("temperatuur", 0.30))

# Temperature score control points (°C, score 0.0–1.0)
# Overridden by score_weights.json if present
_raw_temp_pts = (_USER_CFG or {}).get("temperatuur_score_punten", None)
if _raw_temp_pts is not None:
    TEMPERATURE_SCORE_POINTS = tuple(tuple(p) for p in _raw_temp_pts)
else:
    TEMPERATURE_SCORE_POINTS = (
        (-5.0, 0.00),
        ( 2.0, 0.35),
        ( 8.0, 0.60),
        (10.0, 1.00),
        (25.0, 1.00),
        (27.0, 0.90),
        (35.0, 0.00),
    )

# ---------------------------------------------------------------------------
# Supply corridor — overridden by score_weights.json if present
# ---------------------------------------------------------------------------
_ac = (_USER_CFG or {}).get("aanvoer_corridor", {})
SUPPLY_FRANCE_LAT_MIN   = 43.0  # Southern France
SUPPLY_FRANCE_LAT_MAX   = 49.5  # Northern France / Belgian border
SUPPLY_SPAIN_LAT_MIN    = 36.0  # Tarifa / Southern Spain
SUPPLY_SPAIN_LAT_MAX    = 43.0  # Northern Spain
SUPPLY_CORRIDOR_LON_MIN = -2.0  # Western edge of migration route
SUPPLY_CORRIDOR_LON_MAX = 10.0  # Eastern edge of migration route
SUPPLY_LAG_FRANCE       = int(_ac.get("lag_france", 1))
SUPPLY_LAG_SPAIN        = int(_ac.get("lag_spain", 2))
SUPPLY_FRANCE_WEIGHT    = float(_ac.get("france_weight", 0.60))
SUPPLY_SPAIN_WEIGHT     = float(_ac.get("spain_weight", 0.40))
SUPPLY_FACTOR_FLOOR     = float(_ac.get("factor_floor", 0.30))
SUPPLY_FACTOR_RANGE     = float(_ac.get("factor_range", 0.70))
DEFAULT_CORRIDOR_SCORE  = 0.50  # Fallback when corridor is empty

_TF = TimezoneFinder()


# ---------------------------------------------------------------------------
# Land / UK filter
# ---------------------------------------------------------------------------

_EXCLUDED_TIMEZONES = frozenset({
    "Europe/London",       # Great Britain & Northern Ireland
    "Europe/Dublin",       # Ireland
    "Europe/Isle_of_Man",  # Isle of Man
})


def is_geldig_punt(lat: float, lon: float) -> bool:
    """Return True als het punt op land valt en niet in een uitgesloten gebied.

    Uitgesloten: oceaan/zee, Groot-Brittannië, Noord-Ierland, Ierland, Man-eiland.
    TimezoneFinder retourneert None voor oceanen, maar Etc/GMT* voor open zee.
    Beide worden als 'in zee' beschouwd.
    """
    tz = _TF.timezone_at(lat=lat, lng=lon)
    if tz is None:
        return False          # oceaan / diepe zee
    if tz.startswith("Etc/"):
        return False          # open zee (UTC-offset tijdzones)
    if tz in _EXCLUDED_TIMEZONES:
        return False          # uitgesloten regio's
    return True


# ---------------------------------------------------------------------------
# Grid generation
# ---------------------------------------------------------------------------

def build_grid_points() -> list[dict]:
    """Return land grid points anchored at Tarifa.

    Only valid land grid points are included.  UK, Ireland and Isle of Man remain
    excluded throughout.
    """
    lats: set[float] = set()
    n = 0
    while True:
        lat = round(ANCHOR_LAT + n * LAT_STEP, 1)
        if lat > LAT_MAX:
            break
        if lat >= LAT_MIN:
            lats.add(lat)
        n += 1
    n = -1
    while True:
        lat = round(ANCHOR_LAT + n * LAT_STEP, 1)
        if lat < LAT_MIN:
            break
        if lat <= LAT_MAX:
            lats.add(lat)
        n -= 1

    lons: set[float] = set()
    n = 0
    while True:
        lon = round(ANCHOR_LON + n * LON_STEP, 1)
        if lon > LON_MAX:
            break
        if lon >= LON_MIN:
            lons.add(lon)
        n += 1
    n = -1
    while True:
        lon = round(ANCHOR_LON + n * LON_STEP, 1)
        if lon < LON_MIN:
            break
        if lon <= LON_MAX:
            lons.add(lon)
        n -= 1

    # First pass: collect all valid land points
    land_set: set[tuple[float, float]] = set()
    for lat in lats:
        for lon in lons:
            if is_geldig_punt(lat, lon):
                land_set.add((lat, lon))

    # Second pass: build final list with land points only
    points = []
    for lat in sorted(lats):
        for lon in sorted(lons):
            if (lat, lon) in land_set:
                points.append({"latitude": lat, "longitude": lon})
    return points


# ---------------------------------------------------------------------------
# Weather fetching (6-day hourly forecast)
# ---------------------------------------------------------------------------

def fetch_forecast_weather(lat: float, lon: float) -> dict | None:
    """Fetch 6-day hourly forecast for one point from Open-Meteo."""
    params = urllib.parse.urlencode(
        {
            "latitude":     lat,
            "longitude":    lon,
            "hourly": (
                "temperature_2m,wind_speed_10m,wind_direction_10m,"
                "precipitation,visibility,cloud_cover,"
                "pressure_msl,cape,boundary_layer_height"
            ),
            "timezone":     "UTC",
            "forecast_days": FORECAST_DAYS,
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{params}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return json.load(resp).get("hourly")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Migration score (extended)
# ---------------------------------------------------------------------------

def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def interpolate_piecewise_score(value: float, points: tuple[tuple[float, float], ...]) -> float:
    if not points:
        return 0.0
    if value <= points[0][0]:
        return clamp(points[0][1], 0.0, 1.0)
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if value <= x1:
            if x1 == x0:
                return clamp(y1, 0.0, 1.0)
            ratio = (value - x0) / (x1 - x0)
            return clamp(y0 + ratio * (y1 - y0), 0.0, 1.0)
    return clamp(points[-1][1], 0.0, 1.0)


def wind_direction_label(degrees: float) -> str:
    return WIND_DIRECTION_LABELS[round((degrees % 360.0) / 22.5) % 16]


def bene_spring_wind_override(direction_deg: float, speed_kmh: float) -> str | None:
    direction = wind_direction_label(direction_deg)
    if direction in SPRING_ZERO_WIND_ALL_SPEEDS:
        return "zero"
    if direction in SPRING_ZERO_WIND_STRICTLY_ABOVE_3BF and speed_kmh > BENE_WIND_SPEED_3BF_MAX:
        return "zero"
    if direction in SPRING_MAX_WIND_STRICTLY_BELOW_3BF and speed_kmh < BENE_WIND_SPEED_3BF:
        return "max"
    return None


def temperature_score(temp_c: float) -> float:
    return interpolate_piecewise_score(temp_c, TEMPERATURE_SCORE_POINTS)


def compute_migration_score(
    weather: dict | None,
    lat: float = 0.0,
    lon: float = 0.0,
) -> tuple[float, float]:
    """
    Return (score, confidence) where score ∈ [0, 1].

    Weights:
      70 % wind direction     (regionally corrected — see below)
      30 % temperature        (manually adjustable via TEMPERATURE_SCORE_POINTS)

    Region-specific BE/NL corrections have been disabled: all points use the
    general logic to produce a uniform treatment of the raster area.
    """
    if not weather:
        return 0.5, 0.3

    wind_speed = float(weather.get("wind_speed_10m", 0))
    wind_dir   = float(weather.get("wind_direction_10m", 180))
    temp       = float(weather.get("temperature_2m", 12))

    # Use general scoring (no BE/NL special casing)
    south_score = (1.0 - math.cos(math.radians(wind_dir))) / 2.0
    west_component = max(0.0, -math.sin(math.radians(wind_dir)))
    is_nw_w_strong = (WIND_NW_W_DIR_MIN <= wind_dir <= WIND_NW_W_DIR_MAX) and (wind_speed >= BENE_WIND_SPEED_7BF)
    if is_nw_w_strong:
        wind_dir_score = clamp(south_score + west_component * WIND_SEA_BONUS, 0.0, 1.0)
    else:
        wind_dir_score = max(0.0, south_score - WIND_WEST_PENALTY * west_component)

    temp_score = temperature_score(temp)

    score = clamp(
        SCORE_WEIGHT_WIND_DIRECTION * wind_dir_score
        + SCORE_WEIGHT_TEMPERATURE * temp_score,
        0.0,
        1.0,
    )
    confidence = clamp(0.50 + 0.40 * math.sqrt(score), 0.0, 1.0)
    return round(score, 3), round(confidence, 3)


def score_to_class(score: float) -> str:
    if score >= 0.90:
        return "Uitstekend"
    elif score >= 0.80:
        return "Zeer goed"
    elif score >= 0.70:
        return "Goed"
    elif score >= 0.60:
        return "Vrij goed"
    elif score >= 0.50:
        return "Redelijk"
    elif score >= 0.40:
        return "Matig"
    elif score >= 0.30:
        return "Ongunstig"
    elif score >= 0.20:
        return "Slecht"
    elif score >= 0.10:
        return "Zeer slecht"
    return "Verwaarloosbaar"


def flight_altitude(wind_speed_kmh: float) -> str:
    """
    Return expected flight altitude label based on wind speed.

    Higher wind (5–6 Bf, 29–49 km/h) forces birds lower → better observable.
    Calm conditions → birds fly high → less often noticed.

      0–2 Bf (< 12 km/h)  : HIGH  — hard to see
      3–4 Bf (12–28 km/h) : MIDDLE — moderate visibility
      5–6 Bf (29–49 km/h) : LOW   — well observable
      ≥ 7 Bf (≥ 50 km/h)  : migration suppressed
    """
    if wind_speed_kmh >= VLIEGHOOGTE_GESTOPT_THRESHOLD:
        return "Trek beperkt"
    elif wind_speed_kmh >= VLIEGHOOGTE_LAAG_MIN:
        return "Laag"
    elif wind_speed_kmh >= VLIEGHOOGTE_MIDDEL_MIN:
        return "Middel"
    return "Hoog"


# ---------------------------------------------------------------------------
# Build payload
# ---------------------------------------------------------------------------

def apply_supply_chain_correction(results: list[dict]) -> list[dict]:
    """
    Adjust BE/NL migration scores based on upstream supply from Spain & France.

    Method mirrors _pas_aanvoer_toe() in the Streamlit app:
    - France supply: day max(0, d-1)
    - Spain supply : day max(0, d-2)
    - supply_factor = 0.30 + 0.70 × (0.60 × fr + 0.40 × sp)  [floor 0.30]
    - adjusted_score = raw_score × supply_factor
    """
    # Pre-compute per-day corridor averages
    france_avg: list[float] = []
    spain_avg:  list[float] = []
    for day_idx in range(FORECAST_DAYS):
        fr = [
            r["days"][day_idx]["score"] for r in results
            if SUPPLY_FRANCE_LAT_MIN <= r["latitude"] <= SUPPLY_FRANCE_LAT_MAX
            and SUPPLY_CORRIDOR_LON_MIN <= r["longitude"] <= SUPPLY_CORRIDOR_LON_MAX
        ]
        sp = [
            r["days"][day_idx]["score"] for r in results
            if SUPPLY_SPAIN_LAT_MIN <= r["latitude"] <= SUPPLY_SPAIN_LAT_MAX
            and SUPPLY_CORRIDOR_LON_MIN <= r["longitude"] <= SUPPLY_CORRIDOR_LON_MAX
        ]
        france_avg.append(sum(fr) / len(fr) if fr else DEFAULT_CORRIDOR_SCORE)
        spain_avg.append(sum(sp) / len(sp) if sp else DEFAULT_CORRIDOR_SCORE)

    for r in results:
        lat = r["latitude"]
        lon = r["longitude"]
        if not (BENE_LAT_MIN <= lat <= BENE_LAT_MAX and BENE_LON_MIN <= lon <= BENE_LON_MAX):
            continue
        for day_idx, day_data in enumerate(r["days"]):
            fr_day        = max(0, day_idx - SUPPLY_LAG_FRANCE)
            sp_day        = max(0, day_idx - SUPPLY_LAG_SPAIN)
            fr_supply     = france_avg[fr_day]
            sp_supply     = spain_avg[sp_day]
            supply_factor = round(
                SUPPLY_FACTOR_FLOOR + SUPPLY_FACTOR_RANGE
                * (SUPPLY_FRANCE_WEIGHT * fr_supply + SUPPLY_SPAIN_WEIGHT * sp_supply),
                3,
            )
            raw_score     = day_data["score"]
            adj_score     = round(min(1.0, max(0.0, raw_score * supply_factor)), 3)
            day_data["score"]            = adj_score
            day_data["class"]            = score_to_class(adj_score)
            day_data["supply_factor"]    = supply_factor
            day_data["supply_france"]    = round(fr_supply, 3)
            day_data["supply_spain"]     = round(sp_supply, 3)
    return results


def process_point(point: dict) -> dict:
    """Fetch forecast and compute per-day scores for one grid point."""
    lat = point["latitude"]
    lon = point["longitude"]
    hourly = fetch_forecast_weather(lat, lon)
    in_bene = (
        BENE_LAT_MIN <= lat <= BENE_LAT_MAX
        and BENE_LON_MIN <= lon <= BENE_LON_MAX
    )
    days = []
    for day_idx in range(FORECAST_DAYS):
        cape_list = (hourly.get("cape") or [CAPE_DEFAULT] * FORECAST_HOURS) if hourly else [CAPE_DEFAULT] * FORECAST_HOURS
        blh_list  = (hourly.get("boundary_layer_height") or [BLH_DEFAULT] * FORECAST_HOURS) if hourly else [BLH_DEFAULT] * FORECAST_HOURS

        # Compute hourly scores for all 24 hours; use the daily average (not just midday)
        hourly_scores: list[float] = []
        for hour in range(24):
            hour_idx = day_idx * 24 + hour
            if hourly:
                try:
                    hour_weather = {
                        "temperature_2m":       hourly["temperature_2m"][hour_idx],
                        "wind_speed_10m":        hourly["wind_speed_10m"][hour_idx],
                        "wind_direction_10m":    hourly["wind_direction_10m"][hour_idx],
                        "precipitation":         hourly["precipitation"][hour_idx],
                        "visibility":            hourly["visibility"][hour_idx],
                        "cloud_cover":           hourly["cloud_cover"][hour_idx],
                        "pressure_msl":          hourly["pressure_msl"][hour_idx],
                        "cape":                  cape_list[hour_idx],
                        "boundary_layer_height": blh_list[hour_idx],
                    }
                    h_score, _ = compute_migration_score(hour_weather, lat=lat, lon=lon)
                    hourly_scores.append(h_score)
                except (IndexError, KeyError, TypeError):
                    hourly_scores.append(0.5)
            else:
                hourly_scores.append(0.5)

        # Daily score = average over all 24 hours (previously: midday snapshot only)
        score = round(sum(hourly_scores) / len(hourly_scores), 3) if hourly_scores else 0.5
        confidence = round(0.50 + 0.40 * math.sqrt(score), 3)

        # Keep midday weather snapshot for display / reference purposes
        midday_idx = day_idx * 24 + 12
        if hourly:
            try:
                weather = {
                    "temperature_2m":       hourly["temperature_2m"][midday_idx],
                    "wind_speed_10m":        hourly["wind_speed_10m"][midday_idx],
                    "wind_direction_10m":    hourly["wind_direction_10m"][midday_idx],
                    "precipitation":         hourly["precipitation"][midday_idx],
                    "visibility":            hourly["visibility"][midday_idx],
                    "cloud_cover":           hourly["cloud_cover"][midday_idx],
                    "pressure_msl":          hourly["pressure_msl"][midday_idx],
                    "cape":                  cape_list[midday_idx],
                    "boundary_layer_height": blh_list[midday_idx],
                }
            except (IndexError, KeyError, TypeError):
                weather = None
        else:
            weather = None

        wind_spd = float(weather.get("wind_speed_10m", 0)) if weather else 0.0
        days.append({
            "day_offset":   day_idx,
            "score":        score,
            "confidence":   confidence,
            "class":        score_to_class(score),
            "vlieghoogte":  flight_altitude(wind_spd),
            "be_nl_zone":   in_bene,
            "weather":      weather,
        })
    return {
        "latitude":     lat,
        "longitude":    lon,
        "days":         days,
    }


def build_payload() -> dict:
    grid_points = build_grid_points()
    today = date.today()
    day_dates = [(today + timedelta(days=i)).isoformat() for i in range(FORECAST_DAYS)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(executor.map(process_point, grid_points))

    # Apply upstream supply chain correction for BE/NL grid points
    # Supply-chain correction disabled: treat BE/NL points like the rest of Europe
    # results = apply_supply_chain_correction(results)

    # Per-day aggregate scores
    day_aggregates = []
    for day_idx in range(FORECAST_DAYS):
        day_scores = [r["days"][day_idx]["score"] for r in results]
        valid_conf = [
            r["days"][day_idx]["confidence"]
            for r in results
            if r["days"][day_idx]["weather"] is not None
        ]
        day_aggregates.append({
            "date":                 day_dates[day_idx],
            "day_offset":           day_idx,
            "aggregate_score":      round(sum(day_scores) / len(day_scores), 3) if day_scores else 0.5,
            "aggregate_confidence": round(sum(valid_conf) / len(valid_conf), 3) if valid_conf else 0.3,
        })

    return {
        "updated_at":    datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "ttl_minutes":   60,
        "source":        "bmwt-github-actions-v4-tarifa-raster-8day-no-supply-chain-24h-avg",
        "raster": {
            "anchor_lat":    ANCHOR_LAT,
            "anchor_lon":    ANCHOR_LON,
            "lat_step":      LAT_STEP,
            "lon_step":      LON_STEP,
            "point_count":   len(results),
            "forecast_days": FORECAST_DAYS,
        },
        "day_aggregates": day_aggregates,
        "grid_points":    results,
    }


# ---------------------------------------------------------------------------
# Atomic file write
# ---------------------------------------------------------------------------

def write_json_atomically(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    output_path = Path(os.environ.get("BMWT_MIGRATION_OUTPUT_PATH", str(OUTPUT_PATH)))
    print(
        f"Building 8-day migration raster payload "
        f"({LAT_MAX - LAT_MIN:.0f}° lat × {LON_MAX - LON_MIN:.1f}° lon, "
        f"step {LAT_STEP}°×{LON_STEP}°, land/UK filtered)…"
    )
    payload = build_payload()
    write_json_atomically(output_path, payload)
    n = payload["raster"]["point_count"]
    print(f"Wrote {n} land points × {FORECAST_DAYS} days to {output_path}")
    for da in payload["day_aggregates"]:
        print(
            f"  Day +{da['day_offset']} ({da['date']}): "
            f"score={da['aggregate_score']:.3f}  "
            f"conf={da['aggregate_confidence']:.3f}"
        )
