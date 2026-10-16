"""Flask dashboard for the Automatic Pro Trips Excel export.

Parses the xlsx, decodes the embedded Google-encoded route polylines,
and serves a compact JSON endpoint consumed by the frontend.
"""

import datetime as _dt
import json
import math
import os
import re

import pandas as pd
from flask import Flask, jsonify, render_template

DATA_FILES = ["Automatic Pro Trips.xlsx", "Automatic Pro Trips (1).xlsx"]

# 0-indexed column layout of the export
COLS = {
    "vehicle": 0,
    "started_at": 1,
    "ended_at": 2,
    "distance": 3,
    "duration": 4,
    "mpg": 5,
    "fuel_cost": 6,
    "fuel_used": 7,
    "hard_acc": 8,
    "hard_brakes": 9,
    "over_70": 10,
    "over_75": 11,
    "over_80": 12,
    "map_url": 13,
    "start_lng": 14,
    "start_lat": 15,
    "start_link": 16,
    "end_lng": 17,
    "end_lat": 18,
    "end_link": 19,
}

_TIME_FMT = "%B %d, %Y at %I:%M%p"


def _to_f(value):
    try:
        f = float(value)
        return f if math.isfinite(f) else 0.0
    except (TypeError, ValueError):
        return 0.0


def parse_start(raw):
    """Parse the 'July 28, 2017 at 01:52PM' style datetime."""
    if isinstance(raw, _dt.datetime):
        return raw
    s = str(raw).strip()
    try:
        return _dt.datetime.strptime(s, _TIME_FMT)
    except ValueError:
        return None


def duration_hours(raw):
    """Duration is stored either as a fraction of a day or as 'HH:MM'."""
    s = str(raw).strip()
    if ":" in s:
        parts = s.split(":")
        try:
            return int(parts[0]) + int(parts[1]) / 60.0
        except ValueError:
            return 0.0
    try:
        return float(s) * 24.0
    except ValueError:
        return 0.0


def decode_polyline(encoded):
    """Decode a Google-encoded polyline string into [(lat, lng), ...]."""
    coords = []
    lat = lng = 0
    i = 0
    n = len(encoded)

    def read():
        nonlocal i
        result = 0
        shift = 0
        while True:
            b = ord(encoded[i]) - 63
            i += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        return ~(result >> 1) if result & 1 else result >> 1

    while i < n:
        try:
            dlat = read()
            dlng = read()
        except IndexError:
            break
        lat += dlat
        lng += dlng
        coords.append((lat / 1e5, lng / 1e5))
    return coords


def decimate(coords, max_points=100):
    if len(coords) <= max_points:
        return coords
    step = math.ceil(len(coords) / max_points)
    return coords[::step]


def extract_polyline(map_url):
    m = re.search(r"path=enc:(.*?)&", str(map_url))
    if not m:
        return []
    try:
        return decimate(decode_polyline(m.group(1)))
    except Exception:
        return []


def row_to_trip(vals):
    start = parse_start(vals[COLS["started_at"]] if len(vals) > COLS["started_at"] else None)
    if start is None:
        return None
    ended = parse_start(vals[COLS["ended_at"]] if len(vals) > COLS["ended_at"] else None) or start
    dist = _to_f(vals[COLS["distance"]] if len(vals) > COLS["distance"] else 0)
    dur_h = duration_hours(vals[COLS["duration"]] if len(vals) > COLS["duration"] else 0)

    route = extract_polyline(vals[COLS["map_url"]] if len(vals) > COLS["map_url"] else "")

    start_lat = round(_to_f(vals[COLS["start_lat"]] if len(vals) > COLS["start_lat"] else 0), 5)
    start_lng = round(_to_f(vals[COLS["start_lng"]] if len(vals) > COLS["start_lng"] else 0), 5)
    end_lat = round(_to_f(vals[COLS["end_lat"]] if len(vals) > COLS["end_lat"] else 0), 5)
    end_lng = round(_to_f(vals[COLS["end_lng"]] if len(vals) > COLS["end_lng"] else 0), 5)
    place = PLACES.get(f"{end_lat:.5f},{end_lng:.5f}", {})
    start_place = PLACES.get(f"{start_lat:.5f},{start_lng:.5f}", {})

    return {
        "start": start.isoformat(),
        "end": ended.isoformat(),
        "ts": int(start.timestamp() * 1000),
        "dist": round(dist, 2),
        "dur_min": round(dur_h * 60, 1),
        "mpg": round(_to_f(vals[COLS["mpg"]] if len(vals) > COLS["mpg"] else 0), 1),
        "cost": round(_to_f(vals[COLS["fuel_cost"]] if len(vals) > COLS["fuel_cost"] else 0), 2),
        "fuel": round(_to_f(vals[COLS["fuel_used"]] if len(vals) > COLS["fuel_used"] else 0), 2),
        "hard_acc": int(_to_f(vals[COLS["hard_acc"]] if len(vals) > COLS["hard_acc"] else 0)),
        "hard_brakes": int(_to_f(vals[COLS["hard_brakes"]] if len(vals) > COLS["hard_brakes"] else 0)),
        "over_70": int(_to_f(vals[COLS["over_70"]] if len(vals) > COLS["over_70"] else 0)),
        "over_75": int(_to_f(vals[COLS["over_75"]] if len(vals) > COLS["over_75"] else 0)),
        "over_80": int(_to_f(vals[COLS["over_80"]] if len(vals) > COLS["over_80"] else 0)),
        "start_lat": start_lat,
        "start_lng": start_lng,
        "end_lat": end_lat,
        "end_lng": end_lng,
        "start_link": str(vals[COLS["start_link"]] if len(vals) > COLS["start_link"] else ""),
        "end_link": str(vals[COLS["end_link"]] if len(vals) > COLS["end_link"] else ""),
        "start_label": start_place.get("label") or "Unknown",
        "start_cat": start_place.get("category") or "Unknown",
        "dest_label": place.get("label") or "Unknown",
        "dest_cat": place.get("category") or "Unknown",
        "route": [[round(lt, 5), round(ln, 5)] for lt, ln in route],
    }


def load_trips():
    trips = []
    seen = set()
    for path in DATA_FILES:
        try:
            df = pd.read_excel(path, header=None, dtype=str)
        except FileNotFoundError:
            continue
        for _, row in df.iterrows():
            trip = row_to_trip(row.tolist())
            if trip is None:
                continue
            key = (trip["ts"], trip["dist"])
            if key in seen:
                continue
            seen.add(key)
            trips.append(trip)
    trips.sort(key=lambda t: t["ts"])
    return trips


def _load_places():
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "places_cache.json")
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


PLACES = _load_places()

TRIPS = load_trips()

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html", trip_count=len(TRIPS))


@app.route("/api/trips")
def api_trips():
    return jsonify(TRIPS)


@app.route("/api/meta")
def api_meta():
    if not TRIPS:
        return jsonify({"count": 0})
    return jsonify(
        {
            "count": len(TRIPS),
            "first": TRIPS[0]["start"],
            "last": TRIPS[-1]["start"],
            "total_dist": round(sum(t["dist"] for t in TRIPS), 1),
            "total_fuel": round(sum(t["fuel"] for t in TRIPS), 1),
            "total_cost": round(sum(t["cost"] for t in TRIPS), 2),
        }
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
