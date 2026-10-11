"""Builds one delivery day from real data.

Stops come from two real Amazon routes that left the DLA7 depot (Chino, CA) on
22 Jul 2018 (Amazon Last Mile Routing Research Challenge, CC BY-NC 4.0).
Business customers are real places from OpenStreetMap inside the same area.
Road travel times come from OSRM on OpenStreetMap roads.

What is invented, and said so: delivery promises (Amazon's sample carries
almost none), delivery fees, and which van starts with which stops.

    python -m detour.prep
"""
import json, math, random
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OSRM = "https://router.project-osrm.org"
DAY = "2018-07-22"
VANS = 5
HOMES = 96
random.seed(22)

# What each customer was promised (same-morning service), minutes after midnight, local time.
PROMISE = {"pharmacy": 11 * 60 + 30, "clinic": 12 * 60, "restaurant": 10 * 60 + 30, "home": 13 * 60}
FEE = {"pharmacy": 9.99, "clinic": 9.99, "restaurant": 7.99, "home": 5.99}
SERVICE_MIN = {"pharmacy": 4, "clinic": 4, "restaurant": 5, "home": 2}


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def amazon_stops():
    routes = json.loads((DATA / "amazon/new_route_data.json").read_text())
    ids = [k for k, v in routes.items() if v["station_code"] == "DLA7" and v["date_YYYY_MM_DD"] == DAY]
    depot, homes = None, []
    for rid in ids:
        for sid, s in routes[rid]["stops"].items():
            if s["type"] == "Station":
                depot = (s["lat"], s["lng"])
            else:
                homes.append({"src": f"amazon:{rid[8:16]}:{sid}", "lat": s["lat"], "lng": s["lng"]})
    # Every n-th stop keeps the spread of the real routes without 300 stops.
    step = max(1, len(homes) // HOMES)
    return depot, homes[::step][:HOMES]


def osm_businesses(homes):
    els = json.loads((DATA / "osm_places.json").read_text())["elements"]
    want = {"pharmacy": 5, "clinic": 4, "restaurant": 6}
    kind_of = {"pharmacy": "pharmacy", "clinic": "clinic", "doctors": "clinic", "restaurant": "restaurant"}
    pool = {k: [] for k in want}
    for e in els:
        k = kind_of.get(e["tags"].get("amenity"))
        name = e["tags"].get("name")
        lat, lng = (e.get("lat"), e.get("lon")) if "lat" in e else (e["center"]["lat"], e["center"]["lon"])
        if not k or not name:
            continue
        # Only places on the vans' patch: near a real stop (wider for scarce clinics).
        near = min(km((lat, lng), (h["lat"], h["lng"])) for h in homes)
        if near < (1.5 if k == "restaurant" else 3.5):
            pool[k].append({"src": f"osm:{e['type']}/{e['id']}", "name": name, "lat": lat, "lng": lng, "kind": k})
    out = []
    for k, n in want.items():
        random.shuffle(pool[k])
        out += pool[k][:n]
    return out


def osrm_matrix(points):
    """Seconds between every pair, in 50x50 blocks to stay under the demo server's limit."""
    n, B = len(points), 50
    M = [[0] * n for _ in range(n)]
    with httpx.Client(timeout=60) as c:
        for i0 in range(0, n, B):
            for j0 in range(0, n, B):
                src, dst = list(range(i0, min(n, i0 + B))), list(range(j0, min(n, j0 + B)))
                idx = sorted(set(src + dst))
                pos = {p: k for k, p in enumerate(idx)}
                coords = ";".join(f"{points[p][1]:.6f},{points[p][0]:.6f}" for p in idx)
                q = f"sources={';'.join(str(pos[s]) for s in src)}&destinations={';'.join(str(pos[d]) for d in dst)}"
                for attempt in range(4):
                    r = c.get(f"{OSRM}/table/v1/driving/{coords}?{q}")
                    if r.status_code == 200 and r.json().get("code") == "Ok":
                        break
                else:
                    raise RuntimeError(f"OSRM table failed: {r.status_code} {r.text[:200]}")
                d = r.json()["durations"]
                for a, s in enumerate(src):
                    for b, t in enumerate(dst):
                        M[s][t] = round(d[a][b] or 0)
    return M


def street_names(orders, only_missing=False):
    """The street each stop is on, for messages a person can read.

    If the stop's own road has no name, fall back to the nearest road that does."""
    with httpx.Client(timeout=30) as c:
        for o in orders:
            if only_missing and o.get("street"):
                continue
            try:
                r = c.get(f"{OSRM}/nearest/v1/driving/{o['lng']:.6f},{o['lat']:.6f}?number=10").json()
                o["street"] = next((w["name"] for w in r["waypoints"] if w["name"]), "")
            except Exception:
                o["street"] = o.get("street", "")


def fill_missing_streets():
    """Patch data/day.json in place: names only the stops that have none, nothing else moves."""
    path = DATA / "day.json"
    day = json.loads(path.read_text())
    todo = [o for o in day["orders"] if not o.get("street")]
    street_names(todo, only_missing=True)
    # Still nothing (a car park, a private lane): borrow the street of the closest stop that has one.
    named = [o for o in day["orders"] if o.get("street")]
    for o in todo:
        if not o["street"] and named:
            near = min(named, key=lambda x: km((o["lat"], o["lng"]), (x["lat"], x["lng"])))
            o["street"] = near["street"]
    path.write_text(json.dumps(day))
    print(f"filled {sum(1 for o in todo if o['street'])}/{len(todo)} missing street names")


def main():
    depot, homes = amazon_stops()
    biz = osm_businesses(homes)
    orders = []
    for h in homes:
        orders.append({**h, "kind": "home", "name": None})
    orders += biz
    random.shuffle(orders)
    for i, o in enumerate(orders):
        o["id"] = f"O{i + 1:03d}"
        o["promise_min"] = PROMISE[o["kind"]]
        o["fee"] = FEE[o["kind"]]
        o["service_min"] = SERVICE_MIN[o["kind"]]
    street_names(orders)
    points = [depot] + [(o["lat"], o["lng"]) for o in orders]
    print(f"{len(homes)} Amazon stops + {len(biz)} OSM businesses; asking OSRM for a {len(points)}x{len(points)} matrix…")
    M = osrm_matrix(points)
    day = {
        "date": DAY,
        "depot": {"lat": depot[0], "lng": depot[1], "name": "Amazon DLA7 depot, Chino CA"},
        "start_min": 9 * 60,
        "vans": [{"id": f"V{i + 1}", "name": f"Van {i + 1}"} for i in range(VANS)],
        "orders": orders,
        "matrix_s": M,
        "sources": {
            "stops": "Amazon Last Mile Routing Research Challenge 2021, CC BY-NC 4.0",
            "businesses": "OpenStreetMap contributors, ODbL",
            "roads": "OSRM on OpenStreetMap",
            "invented": "delivery promises, fees, starting van assignment",
        },
    }
    (DATA / "day.json").write_text(json.dumps(day))
    print("wrote data/day.json:", {k: sum(1 for o in orders if o["kind"] == k) for k in PROMISE})


if __name__ == "__main__":
    main()
