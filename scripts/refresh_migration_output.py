#!/usr/bin/env python3
"""
Refresh migration prediction data for BMWT.

Fetches a 6-day hourly weather forecast for a ~100 × 100 km raster over
Western Europe (anchored on Tarifa, Spain) and computes a migration-
favourability score for each grid point per day.

Grid points in the sea or in the United Kingdom are excluded automatically
via TimezoneFinder.

Score  0.0 = extremely unfavourable (BLUE)
Score  1.0 = extremely favourable   (RED)

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
FORECAST_DAYS = 6     # today + 5 days ahead
FORECAST_HOURS = FORECAST_DAYS * 24   # = 144 hourly values

# Flight altitude thresholds (km/h)
VLIEGHOOGTE_GESTOPT_MIN = 50   # ≥ 7 Bf: migration suppressed
VLIEGHOOGTE_LAAG_MIN    = 29   # 5–6 Bf: birds fly low (good observability)
VLIEGHOOGTE_MIDDEL_MIN  = 12   # 3–4 Bf: mid-altitude

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

# Beaufort speed thresholds (km/h)
BENE_WIND_SPEED_1BF =  1.0    # Bf 1 lower bound
BENE_WIND_SPEED_3BF = 12.0    # Bf 3 lower bound (optimal range start)
BENE_WIND_SPEED_5BF = 38.0    # Bf 5 upper bound (optimal range end)
BENE_WIND_SPEED_7BF = 50.0    # Bf 7 lower bound (migration suppressed)

_TF = TimezoneFinder()


# ---------------------------------------------------------------------------
# Land / UK filter
# ---------------------------------------------------------------------------

def is_geldig_punt(lat: float, lon: float) -> bool:
    """Return True als het punt op land valt en niet in het Verenigd Koninkrijk.

    TimezoneFinder retourneert None voor oceanen, maar Etc/GMT* voor open zee.
    Beide worden als 'in zee' beschouwd.
    """
    tz = _TF.timezone_at(lat=lat, lng=lon)
    if tz is None:
        return False          # oceaan / diepe zee
    if tz.startswith("Etc/"):
        return False          # open zee (UTC-offset tijdzones)
    if tz == "Europe/London":
        return False          # Verenigd Koninkrijk
    return True


# ---------------------------------------------------------------------------
# Grid generation
# ---------------------------------------------------------------------------

def build_grid_points() -> list[dict]:
    """Return land-only grid points anchored at Tarifa, covering Western Europe."""
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

    points = []
    for lat in sorted(lats):
        for lon in sorted(lons):
            if is_geldig_punt(lat, lon):
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


def compute_migration_score(
    weather: dict | None,
    lat: float = 0.0,
    lon: float = 0.0,
) -> tuple[float, float]:
    """
    Return (score, confidence) where score ∈ [0, 1].

    Weights:
      35 % wind direction     (regionally corrected — see below)
      20 % precipitation      (dry = good; rain fronts cause stopover)
      10 % sea-level pressure (> 1015 hPa anticyclone = stable = good)
      10 % visibility         (clear = good)
      10 % wind speed         (regionally corrected — see below)
       5 % temperature        (8–20 °C = optimal)
       5 % boundary-layer height (BLH > 1500 m = good thermals for soaring birds)
       5 % CAPE               (convective energy — thermal indicator)

    BE/NL regional correction (BENE_LAT/LON_MIN/MAX):
      SE wind (≈ 135°, 3–5 Bf) is optimal for Belgium/Netherlands — birds are
      pushed from central France toward the North Sea coast.
      Wind direction formula shifts optimal from 180° (S, general) to 135° (SE):
        score = (1 - cos(wind_dir + 45°)) / 2  →  max at 135°
      Wind speed optimum shifts to 3–5 Bf (12–38 km/h).
    """
    if not weather:
        return 0.5, 0.3

    wind_speed = float(weather.get("wind_speed_10m", 0))
    wind_dir   = float(weather.get("wind_direction_10m", 180))
    temp       = float(weather.get("temperature_2m", 12))
    precip     = float(weather.get("precipitation", 0))
    visibility = float(weather.get("visibility", 10000))
    pressure   = float(weather.get("pressure_msl", 1013))
    cape       = float(weather.get("cape", 0))
    blh        = float(weather.get("boundary_layer_height", 500))

    in_bene = (
        BENE_LAT_MIN <= lat <= BENE_LAT_MAX
        and BENE_LON_MIN <= lon <= BENE_LON_MAX
    )

    if in_bene:
        # Asymmetric directional score peaked at ZO (135°):
        #   ZO (135°) > ZZO (157.5°) > OZO (112.5°) > Z (180°) > O (90°) > N/W ≈ 0
        delta = ((wind_dir - BENE_WIND_OPT_DIR) + 180.0) % 360.0 - 180.0
        if delta >= 0:
            wind_dir_score = max(
                0.0, math.cos(math.radians(delta * 180.0 / BENE_WIND_FALLOFF_S))
            )
        else:
            wind_dir_score = max(
                0.0, math.cos(math.radians(abs(delta) * 180.0 / BENE_WIND_FALLOFF_E))
            )
        # Tiered speed score: 3–5 Bf optimal > 1–3 Bf good > calm/storm
        if wind_speed < BENE_WIND_SPEED_1BF:
            wind_speed_score = 0.2
        elif wind_speed < BENE_WIND_SPEED_3BF:
            wind_speed_score = 0.2 + (
                (wind_speed - BENE_WIND_SPEED_1BF)
                / (BENE_WIND_SPEED_3BF - BENE_WIND_SPEED_1BF)
            ) * 0.8
        elif wind_speed <= BENE_WIND_SPEED_5BF:
            wind_speed_score = 1.0
        elif wind_speed < BENE_WIND_SPEED_7BF:
            wind_speed_score = max(
                0.3, 1.0 - (wind_speed - BENE_WIND_SPEED_5BF)
                / (BENE_WIND_SPEED_7BF - BENE_WIND_SPEED_5BF) * 0.7
            )
        else:
            wind_speed_score = max(0.0, 0.3 - (wind_speed - BENE_WIND_SPEED_7BF) / 30.0)
    else:
        # General: south (180°) = tailwind → 1.0; north (0°/360°) → 0.0
        wind_dir_score = (1.0 - math.cos(math.radians(wind_dir))) / 2.0
        # Optimal 5–25 km/h
        if wind_speed <= 5:
            wind_speed_score = wind_speed / 5.0
        elif wind_speed <= 25:
            wind_speed_score = 1.0
        else:
            wind_speed_score = clamp(1.0 - (wind_speed - 25) / 35.0, 0.0, 1.0)

    # Precipitation: dry = 1.0, 5 mm/h+ = 0.0
    precip_score = clamp(1.0 - precip / 5.0, 0.0, 1.0)

    # Visibility: 10 km+ = 1.0
    vis_score = clamp(visibility / 10000.0, 0.0, 1.0)

    # Temperature: 8–20 °C = optimal
    if 8 <= temp <= 20:
        temp_score = 1.0
    elif temp < 8:
        temp_score = clamp((temp + 5) / 13.0, 0.0, 1.0)
    else:
        temp_score = clamp(1.0 - (temp - 20) / 15.0, 0.0, 1.0)

    # Pressure: high-pressure system → good; 1025+ ≈ 1.0, < 995 ≈ 0.0
    pressure_score = clamp((pressure - 995.0) / 30.0, 0.0, 1.0)

    # Boundary-layer height: higher = better thermals for soaring birds
    blh_score = clamp(blh / 1500.0, 0.0, 1.0)

    # CAPE: moderate = good for thermal soaring; too high = storm risk
    if cape <= 0:
        cape_score = 0.2
    elif cape <= 500:
        cape_score = 0.4 + (cape / 500.0) * 0.5
    elif cape <= 1500:
        cape_score = 0.9 - ((cape - 500) / 1000.0) * 0.5
    else:
        cape_score = clamp(0.4 - (cape - 1500) / 1500.0, 0.0, 1.0)

    # BE/NL: direction 40%, speed 5% (direction is the prime discriminator on
    # the coast; ensures ZO-1Bf > ZZO-3Bf etc. for all 7 tier priorities).
    # General: direction 35%, speed 10%.
    if in_bene:
        score = clamp(
            0.40 * wind_dir_score
            + 0.05 * wind_speed_score
            + 0.20 * precip_score
            + 0.10 * vis_score
            + 0.05 * temp_score
            + 0.10 * pressure_score
            + 0.05 * blh_score
            + 0.05 * cape_score,
            0.0,
            1.0,
        )
    else:
        score = clamp(
            0.35 * wind_dir_score
            + 0.10 * wind_speed_score
            + 0.20 * precip_score
            + 0.10 * vis_score
            + 0.05 * temp_score
            + 0.10 * pressure_score
            + 0.05 * blh_score
            + 0.05 * cape_score,
            0.0,
            1.0,
        )
    confidence = clamp(0.50 + 0.40 * math.sqrt(score), 0.0, 1.0)
    return round(score, 3), round(confidence, 3)


def score_to_class(score: float) -> str:
    if score >= 0.75:
        return "TOP"
    elif score >= 0.50:
        return "GOED"
    elif score >= 0.25:
        return "MATIG"
    return "LAAG"


def flight_altitude(wind_speed_kmh: float) -> str:
    """
    Return expected flight altitude label based on wind speed.

    Higher wind (< 7 Bf / < 50 km/h) forces birds lower → better observable.
    Calm conditions → birds fly high → less often noticed.

      0–2 Bf (< 12 km/h)  : HIGH  — hard to see
      3–4 Bf (12–28 km/h) : MIDDLE — moderate visibility
      5–6 Bf (29–49 km/h) : LOW   — well observable
      ≥ 7 Bf (≥ 50 km/h)  : migration suppressed
    """
    if wind_speed_kmh >= VLIEGHOOGTE_GESTOPT_MIN:
        return "Trek beperkt"
    elif wind_speed_kmh >= VLIEGHOOGTE_LAAG_MIN:
        return "Laag"
    elif wind_speed_kmh >= VLIEGHOOGTE_MIDDEL_MIN:
        return "Middel"
    return "Hoog"


# ---------------------------------------------------------------------------
# Build payload
# ---------------------------------------------------------------------------

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
        midday_idx = day_idx * 24 + 12  # 12:00 UTC
        if hourly:
            try:
                cape_list = hourly.get("cape") or [CAPE_DEFAULT] * FORECAST_HOURS
                blh_list  = hourly.get("boundary_layer_height") or [BLH_DEFAULT] * FORECAST_HOURS
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

        score, confidence = compute_migration_score(weather, lat=lat, lon=lon)
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
        "latitude":  lat,
        "longitude": lon,
        "days":      days,
    }


def build_payload() -> dict:
    grid_points = build_grid_points()
    today = date.today()
    day_dates = [(today + timedelta(days=i)).isoformat() for i in range(FORECAST_DAYS)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(executor.map(process_point, grid_points))

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
        "source":        "bmwt-github-actions-v2-tarifa-raster-6day",
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
        f"Building 6-day migration raster payload "
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
