import sys
sys.path.insert(0, ".")
from app import TRIPS
print("TRIPS loaded:", len(TRIPS))
print("first:", TRIPS[0]["dest_label"], "|", TRIPS[0]["start_label"], "| route pts:", len(TRIPS[0]["route"]))
print("total_dist:", round(sum(t["dist"] for t in TRIPS), 1))
print("labels ok:", all(t["dest_label"] != "Unknown" for t in TRIPS),
      all(t["start_label"] != "Unknown" for t in TRIPS))
