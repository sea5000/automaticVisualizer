"""One-time destination geocoding pipeline.

Clusters the unique trip end coordinates (~130 m cells), reverse-geocodes each
cluster centroid via Geoapify, derives a place/building label plus a destination
type, and writes places_cache.json mapping every unique end coordinate to
{label, category}.

Labels come from the reverse-geocoder (street address for residential, place
name for POIs). Categories are derived deterministically from the place name /
street / result_type (Geoapify's reverse API does not expose categories and its
Places API cannot combine categories reliably, so a keyword classifier stands in
for the categorization step).

Run:  .venv/bin/python geocode.py
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from collections import defaultdict

from app import TRIPS

GEOAPIFY_KEY = os.environ.get("GEOAPIFY_KEY", "")
BASE = os.path.dirname(os.path.abspath(__file__))
CACHE_JSON = os.path.join(BASE, "places_cache.json")
RAW_DIR = os.path.join(BASE, ".geo_cache")
SLEEP = 0.13  # ~8 req/s, polite for the free tier
RETRIES = 4

# ~130 m grid cells (lat/lng degrees at 41 N)
DLAT, DLNG = 0.0012, 0.0015

NON_PLACE_TYPES = {"building", "address", "street", "highway", "postcode", "place"}

CAT_RULES = [
    (r"school|university|college|elementary|academy|preschool|kindergarten|daycare|high\b", "Education"),
    (r"walmart|supercenter|costco|home depot|market|grocery|store|shop|copy|hardware|pharmacy|drug|retail|mall|bakery|furniture|deseret industries|thrift|auto parts|oil change|garage|mechanic|tire|car wash|automotive|napa|auto\b",
     "Retail / Grocery"),
    (r"restaurant|caf[eé]|grill|deli|pizza|burger|taco|taquer[ií]a|kitchen|diner|sushi|steak|bbq|coffee|liquor|bar\b|pub|saloon|tavern|brewery|brew\b|distillery|winery|yurt",
     "Restaurant / Café"),
    (r"cinema|theater|theatre|megaplex|movie|entertainment", "Entertainment"),
    (r"bank|credit union|financial|mortgage", "Banking / Finance"),
    (r"church|chapel|temple|cathedral|meetinghouse|ward\b|lds\b|latter.day", "Place of Worship"),
    (r"hospital|clinic|dentist|medical|health|urgent care|chiropract|dental", "Healthcare"),
    (r"hotel|lodge|resort|worldmark|inn\b|motel|timeshare|cabin|condo|condominium", "Lodging / Resort"),
    (r"park\b|canyon|trail|lake|reservoir|creek|beach|camp\b|golf|mountain|ski|snowbasin|powder|marina|wildlife|forest|spring|falls|picnic",
     "Outdoor / Recreation"),
    (r"station|frontrunner|parking|transit|rail\b|airport|maverik|fuel|gas station", "Transport / Parking"),
    (r"fire\b|police|sheriff|post office|library|city hall|court\b|dmv|government|county", "Government / Public"),
    (r"gym|fitness|recreation|rec center|pool\b|bowling|arena|stadium|field\b|rodeo", "Sports / Recreation"),
    (r"office|plaza|business", "Office / Business"),
]


def classify(name, street, result_type):
    text = " ".join(filter(None, [name, street])).lower()
    for pattern, label in CAT_RULES:
        if re.search(pattern, text):
            return label
    if result_type in NON_PLACE_TYPES:
        return "Home / Residential"
    if result_type == "amenity":
        return "Other Place"
    return "Other"


def clean_name(name):
    n = re.sub(r"\s*\(.*?\)\s*$", "", name or "").strip()
    n = re.sub(r"^\(closed\)\s*", "", n, flags=re.I).strip()
    return n


def short_city(p):
    city, st = p.get("city") or "", p.get("state_code") or ""
    if city and st:
        return f"{city}, {st}"
    return city or p.get("address_line2", "").split(",")[0].strip() or "Unknown"


def residential_label(p):
    parts = []
    if p.get("housenumber") and p.get("street"):
        return f"{p['housenumber']} {p['street']} · {short_city(p)}"
    if p.get("housenumber"):
        parts.append(p["housenumber"])
    if p.get("street"):
        parts.append(p["street"])
    if parts:
        return " · ".join(parts) + " · " + short_city(p)
    return short_city(p)


def pick_label(feats):
    """Return (label, category) for a reverse-geocode feature list."""
    named = None
    for f in feats:
        p = f.get("properties", {})
        name = clean_name(p.get("name"))
        if not name:
            continue
        if p.get("result_type") in NON_PLACE_TYPES:
            continue
        if name.lower() == (p.get("city") or "").lower():
            continue
        named = (p, name)
        break
    if named is not None:
        p, name = named
        return name, classify(name, None, p.get("result_type"))
    p = feats[0].get("properties", {}) if feats else {}
    label = residential_label(p)
    return label, classify(None, p.get("street"), p.get("result_type"))


def reverse_geocode(lat, lng):
    url = "https://api.geoapify.com/v1/geocode/reverse?" + urllib.parse.urlencode(
        {"lat": round(lat, 5), "lon": round(lng, 5), "apiKey": GEOAPIFY_KEY, "limit": 6}
    )
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "trip-dashboard/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 402:
                raise
            if e.code == 429:
                time.sleep(2 * (attempt + 1))
            elif attempt < RETRIES - 1:
                time.sleep(0.5 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < RETRIES - 1:
                time.sleep(0.5 * (attempt + 1))
    return {"features": []}


def build_clusters(trips, start=False):
    cells = defaultdict(list)
    for t in trips:
        la, ln = (t["start_lat"], t["start_lng"]) if start else (t["end_lat"], t["end_lng"])
        cells[(round(la / DLAT), round(ln / DLNG))].append((la, ln))
    clusters = []
    for pts in cells.values():
        clusters.append(
            (
                sum(p[0] for p in pts) / len(pts),
                sum(p[1] for p in pts) / len(pts),
                len(pts),
            )
        )
    clusters.sort(key=lambda c: -c[2])
    return clusters


def geocode_clusters(clusters):
    results = []  # (lat, lng, count, label, category)
    for i, (lat, lng, count) in enumerate(clusters, 1):
        data = load_raw(lat, lng)
        label, category = pick_label(data.get("features", []))
        results.append((lat, lng, count, label, category))
        if i % 25 == 0:
            print(f"  {i}/{len(clusters)} done", flush=True)
    return results


def load_raw(lat, lng):
    key = f"{round(lat, 5)},{round(lng, 5)}"
    path = os.path.join(RAW_DIR, key.replace(".", "_") + ".json")
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    data = reverse_geocode(lat, lng)
    os.makedirs(RAW_DIR, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(data, fh)
    time.sleep(SLEEP)
    return data


def main():
    if not GEOAPIFY_KEY:
        raise SystemExit(
            "GEOAPIFY_KEY is not set. Put it in a local .env (see .env.example) "
            "or export it before running this script. It is never committed here."
        )
    end_results = geocode_clusters(build_clusters(TRIPS))
    print(f"ends: {len(end_results)} clusters", flush=True)
    start_results = geocode_clusters(build_clusters(TRIPS, start=True))
    print(f"starts: {len(start_results)} clusters", flush=True)

    cache = {}
    for t in TRIPS:
        for (la, ln, results) in (
            (t["end_lat"], t["end_lng"], end_results),
            (t["start_lat"], t["start_lng"], start_results),
        ):
            key = f"{la:.5f},{ln:.5f}"
            if key in cache:
                continue
            best = min(
                results,
                key=lambda r: (r[0] - la) ** 2 + (r[1] - ln) ** 2,
            )
            cache[key] = {"label": best[3], "category": best[4]}

    with open(CACHE_JSON, "w") as fh:
        json.dump(cache, fh, indent=0)
    print(f"wrote {CACHE_JSON} ({len(cache)} unique coords)")

    print("\n--- top 25 start clusters ---")
    for lat, lng, count, label, category in start_results[:25]:
        print(f"{count:4d}  {category:22s} {label}")


if __name__ == "__main__":
    main()
