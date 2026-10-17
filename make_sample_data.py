#!/usr/bin/env python3
"""Generate the synthetic sample dataset for the trip dashboard.

Outputs (all fictional; no real fleet data anywhere):
  - "Automatic Pro Trips.xlsx"          : 30 trips, first half of 2018
  - "Automatic Pro Trips (1).xlsx"      : 14 trips, second half of 2018
  - places_cache.json                   : destination labels keyed by round(coord,5):.5f

Schema: no header row, 20 columns, exactly as app.py COLS expects:
vehicle, started_at, ended_at, distance, duration, mpg, fuel_cost, fuel_used,
hard_acc, hard_brakes, over_70, over_75, over_80, map_url (Google-encoded
polyline in path=enc:), start_lng, start_lat, start_link, end_lng, end_lat,
end_link.
"""
import datetime as dt
import json
import math
import random

from openpyxl import Workbook

random.seed(2026)
TF = "%B %d, %Y at %I:%M%p"

# ---------- Google-encoded polyline encoder ----------

def _enc_value(n):
    # zigzag, sign-safe; matches the encoder validated in the round-trip test
    v = n << 1
    v ^= v >> 31
    out = []
    while v >= 0x20:
        out.append(chr(63 + 0x20 + (v & 0x1F)))
        v >>= 5
    out.append(chr(63 + (v & 0x1F)))
    return "".join(out)


def enc_polyline(coords):
    out = []
    plat = plng = 0
    for lat, lng in coords:
        lat_i, lng_i = round(lat * 1e5), round(lng * 1e5)
        out.append(_enc_value(lat_i - plat))
        out.append(_enc_value(lng_i - plng))
        plat, plng = lat_i, lng_i
    return "".join(out)


# ---------- Fictional geography (Ashbrook, a town that does not exist) ----------

HOME = (39.12104, -105.64633)
HOME_LABEL = "Maple & 9th \u00b7 Ashbrook"

DESTINATIONS = [
    ("Summit Market", "Grocery / Retail", 39.12890, -105.63845),
    ("The Copper Kettle", "Restaurant / Caf\u00e9", 39.11872, -105.63012),
    ("Ashbrook Community Center", "Community / Civic", 39.11455, -105.66218),
    ("Pinecrest Elementary", "Education / School", 39.13620, -105.65890),
    ("Larkspur Park", "Park / Recreation", 39.10895, -105.64720),
    ("Highline Clinic", "Healthcare", 39.12680, -105.66740),
    ("Copper Ridge Bank", "Banking / Financial", 39.13210, -105.63480),
    ("Route 9 Fuel", "Fuel / Transportation", 39.10240, -105.65110),
    ("Ashbrook Trailhead", "Outdoor / Recreation", 39.14010, -105.62870),
]

DEST_WEIGHTS = [3, 2, 2, 2, 2, 1, 1, 2, 2]


def route_miles(pts):
    total = 0.0
    for (a, b) in zip(pts[:-1], pts[1:]):
        lat_m = math.radians((a[0] + b[0]) / 2)
        total += math.hypot(b[0] - a[0], (b[1] - a[1]) * math.cos(lat_m))
    return total * 69.0


def make_route(a, b, seed):
    rnd = random.Random(seed)
    n = rnd.randint(18, 40)
    dlat, dlng = b[0] - a[0], b[1] - a[1]
    norm = math.hypot(dlat, dlng) or 1.0
    pl, pn = -dlng / norm, dlat / norm
    amp = rnd.uniform(0.35, 0.9) * norm / 2
    pts = []
    for i in range(n + 1):
        t = i / n
        bend = math.sin(t * math.pi)
        pts.append((
            a[0] + dlat * t + amp * bend * pl + rnd.uniform(-0.0002, 0.0002),
            a[1] + dlng * t + amp * bend * pn + rnd.uniform(-0.0002, 0.0002),
        ))
    pts[0], pts[-1] = a, b  # exact endpoints = marker positions
    return [(round(x, 6), round(y, 6)) for x, y in pts]


def make_trips(n, first_day, seed):
    rnd = random.Random(seed)
    last_day = dt.date(2018, 12, 28)
    trips = []
    for i in range(n):
        day = min(first_day + dt.timedelta(days=rnd.randint(0, 350)), last_day)
        start = dt.datetime(day.year, day.month, day.day, rnd.randint(7, 20), rnd.randint(0, 59))
        name, cat, la, ln = rnd.choices(DESTINATIONS, weights=DEST_WEIGHTS)[0]
        dest_pt = (la, ln)
        route = make_route(HOME, dest_pt, seed * 1000 + i)
        dist = round(route_miles(route) * rnd.uniform(1.15, 1.4), 1)
        dur = int(dist / rnd.uniform(22, 34) * 60) + rnd.randint(5, 45)
        end = start + dt.timedelta(minutes=dur)
        mpg = round(rnd.uniform(14.2, 18.9), 1)
        fuel = round(dist / mpg, 2)
        cost = round(fuel * 3.29, 2)
        hard_acc = rnd.randint(0, 9)
        hard_br = rnd.randint(0, 12)
        ov80 = rnd.randint(0, 2 if dist < 30 else 4)
        ov75 = ov80 + rnd.randint(0, 3)
        ov70 = ov75 + rnd.randint(0, 5)

        poly = enc_polyline(route)
        s_lat, s_lng = round(HOME[0], 5), round(HOME[1], 5)
        e_lat, e_lng = round(dest_pt[0], 5), round(dest_pt[1], 5)
        trips.append([
            "Ford Explorer",
            start.strftime(TF), end.strftime(TF),
            dist, f"{dur // 60}:{dur % 60:02d}",
            mpg, cost, fuel, hard_acc, hard_br, ov70, ov75, ov80,
            f"https://www.google.com/maps/dir/?api=1&path=enc:{poly}&output=embed",
            s_lng, s_lat,
            f"https://www.google.com/maps/place/?q={s_lat}:{s_lng}",
            e_lng, e_lat,
            f"https://www.google.com/maps/place/?q={e_lat}:{e_lng}",
        ])
    trips.sort(key=lambda t: dt.datetime.strptime(t[1], TF))
    return trips


def write_xlsx(path, rows):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)


def round_key(a, b):
    return f"{round(a, 5):.5f},{round(b, 5):.5f}"


def main():
    t1 = make_trips(30, dt.date(2018, 1, 3), 7)
    t2 = make_trips(14, dt.date(2018, 7, 4), 11)
    write_xlsx("Automatic Pro Trips.xlsx", t1)
    write_xlsx("Automatic Pro Trips (1).xlsx", t2)

    cache = {round_key(*HOME): {"label": HOME_LABEL, "category": "Home / Residential"}}
    for name, cat, la, ln in DESTINATIONS:
        cache[round_key(la, ln)] = {"label": name, "category": cat}
    with open("places_cache.json", "w") as fh:
        json.dump(cache, fh, indent=1)

    print(f"xlsx 1: {len(t1)} trips  |  xlsx 2: {len(t2)} trips")
    print(f"places_cache.json: {len(cache)} labels")
    span = (t1 + t2)
    print("first:", span[0][1], " last:", span[-1][1])


if __name__ == "__main__":
    main()
