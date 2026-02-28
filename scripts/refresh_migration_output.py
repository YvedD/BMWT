#!/usr/bin/env python3
"""
Refresh migration prediction data for BMWT.

Fetches real-time weather data for a ~100 × 100 km raster over Western Europe
(anchored on Tarifa, Spain) and computes a migration-favourability score for
each grid point.

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
from datetime import datetime, timezone
from pathlib import Path

OUTPUT_PATH = Path("data/migration/latest.json")

# Grid anchor: Tarifa, Spain (must be an exact grid intersection)
ANCHOR_LAT  = 36.0
ANCHOR_LON  = -5.6
LAT_STEP    = 1.0    # degrees latitude  (~111 km)
LON_STEP    = 1.3    # degrees longitude (~100 km at lat 36°N)
LAT_MIN     = 35.0
LAT_MAX     = 56.0
LON_MIN     = -9.5
LON_MAX     = 15.3

MAX_WORKERS = 20     # parallel HTTP workers


# ---------------------------------------------------------------------------
# Grid generation
# ---------------------------------------------------------------------------

def build_grid_points() -> list[dict]:
    """Return all grid points anchored at Tarifa, covering Western Europe."""
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
            points.append({"latitude": lat, "longitude": lon})
    return points


# ---------------------------------------------------------------------------
# Weather fetching
# ---------------------------------------------------------------------------

def fetch_current_weather(lat: float, lon: float) -> dict | None:
    """Fetch current weather for one point from Open-Meteo (no dependencies)."""
    params = urllib.parse.urlencode(
        {
            "latitude":  lat,
            "longitude": lon,
            "current": (
                "temperature_2m,wind_speed_10m,wind_direction_10m,"
                "precipitation,visibility,cloud_cover"
            ),
            "timezone": "UTC",
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{params}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return json.load(resp).get("current")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Migration score
# ---------------------------------------------------------------------------

def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def compute_migration_score(weather: dict | None) -> tuple[float, float]:
    """
    Return (score, confidence) where score ∈ [0, 1].

    Weights (per AI-Predictor.md):
      wind direction  40 %  (southerly = tailwind for northward spring migration)
      precipitation   25 %  (dry = good)
      wind speed      15 %  (moderate = optimal)
      visibility      10 %  (clear = good)
      temperature     10 %  (8–20 °C = optimal)
    """
    if not weather:
        return 0.5, 0.3

    wind_speed = float(weather.get("wind_speed_10m", 0))
    wind_dir   = float(weather.get("wind_direction_10m", 180))
    temp       = float(weather.get("temperature_2m", 12))
    precip     = float(weather.get("precipitation", 0))
    visibility = float(weather.get("visibility", 10000))

    # Wind direction: south (180°) = tailwind → score 1.0; north (0°/360°) → 0.0
    wind_dir_score = (1.0 - math.cos(math.radians(wind_dir))) / 2.0

    # Wind speed: optimal 5–25 km/h
    if wind_speed <= 5:
        wind_speed_score = wind_speed / 5.0
    elif wind_speed <= 25:
        wind_speed_score = 1.0
    else:
        wind_speed_score = clamp(1.0 - (wind_speed - 25) / 35.0, 0.0, 1.0)

    # Precipitation: dry = 1.0, 5 mm+ = 0.0
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

    score = clamp(
        0.40 * wind_dir_score
        + 0.15 * wind_speed_score
        + 0.25 * precip_score
        + 0.10 * vis_score
        + 0.10 * temp_score,
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


# ---------------------------------------------------------------------------
# Build payload
# ---------------------------------------------------------------------------

def process_point(point: dict) -> dict:
    weather = fetch_current_weather(point["latitude"], point["longitude"])
    score, confidence = compute_migration_score(weather)
    return {
        "latitude":  point["latitude"],
        "longitude": point["longitude"],
        "score":     score,
        "confidence": confidence,
        "class":     score_to_class(score),
        "weather":   weather,
    }


def build_payload() -> dict:
    grid_points = build_grid_points()

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(executor.map(process_point, grid_points))

    valid = [r for r in results if r["weather"] is not None]
    agg_score      = round(sum(r["score"]      for r in results) / len(results), 3) if results else 0.5
    agg_confidence = round(sum(r["confidence"] for r in valid)   / len(valid),   3) if valid   else 0.3

    return {
        "updated_at":    datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "ttl_minutes":   60,
        "source":        "bmwt-github-actions-v1-tarifa-raster",
        "raster": {
            "anchor_lat":    ANCHOR_LAT,
            "anchor_lon":    ANCHOR_LON,
            "lat_step":      LAT_STEP,
            "lon_step":      LON_STEP,
            "point_count":   len(results),
            "valid_points":  len(valid),
            "aggregate_score":      agg_score,
            "aggregate_confidence": agg_confidence,
        },
        "grid_points": results,
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
    print(f"Building migration raster payload ({LAT_MAX - LAT_MIN:.0f}° lat × "
          f"{LON_MAX - LON_MIN:.1f}° lon, step {LAT_STEP}°×{LON_STEP}°)…")
    payload = build_payload()
    write_json_atomically(output_path, payload)
    print(
        f"Wrote {payload['raster']['point_count']} points "
        f"({payload['raster']['valid_points']} with live weather) "
        f"to {output_path}"
    )
    print(
        f"Aggregate score: {payload['raster']['aggregate_score']:.3f}  "
        f"confidence: {payload['raster']['aggregate_confidence']:.3f}"
    )
