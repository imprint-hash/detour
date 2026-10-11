"""Street geometry for the live screen, so vans drive real roads instead of straight lines.

Each leg (stop A to stop B) is fetched once from OSRM and cached in data/legs.json.
Fetching happens in a background thread: until a leg arrives, the van falls back to a
straight line. Headless runs never touch this; only the live screen switches it on.
"""
import json, math, threading, time, queue
from pathlib import Path
import httpx

OSRM = "https://router.project-osrm.org"
CACHE = Path(__file__).resolve().parent.parent / "data" / "legs.json"
MAX_POINTS = 40  # per leg, plenty for a smooth line on screen

_lock = threading.Lock()
_legs = {}
_wanted = queue.Queue()
_queued = set()
_started = False


def _load():
    global _legs
    if CACHE.exists():
        try:
            _legs = {k: v for k, v in json.loads(CACHE.read_text()).items()}
        except Exception:
            _legs = {}


def _save():
    with _lock:
        CACHE.write_text(json.dumps(_legs))


def _thin(pts):
    if len(pts) <= MAX_POINTS:
        return pts
    step = (len(pts) - 1) / (MAX_POINTS - 1)
    return [pts[round(i * step)] for i in range(MAX_POINTS)]


def _worker(ll):
    dirty = 0
    with httpx.Client(timeout=20) as c:
        while True:
            a, b = _wanted.get()
            key = f"{a}-{b}"
            if key in _legs:
                continue
            (la, lo), (lb, lob) = ll(a), ll(b)
            try:
                r = c.get(f"{OSRM}/route/v1/driving/{lo:.6f},{la:.6f};{lob:.6f},{lb:.6f}",
                          params={"overview": "full", "geometries": "geojson"})
                if r.status_code == 200 and r.json().get("code") == "Ok":
                    coords = r.json()["routes"][0]["geometry"]["coordinates"]
                    pts = _thin([[round(y, 6), round(x, 6)] for x, y in coords])
                    with _lock:
                        _legs[key] = pts
                    dirty += 1
                elif r.status_code == 429:
                    time.sleep(2)
                    _queued.discard(key)
                    continue
            except Exception:
                _queued.discard(key)
                time.sleep(1)
                continue
            if dirty >= 10 or _wanted.empty():
                _save()
                dirty = 0
            time.sleep(0.25)  # be gentle with the public OSRM server


def start(ll):
    """Switch street geometry on. `ll(node) -> (lat, lng)`."""
    global _started
    if _started:
        return
    _started = True
    _load()
    threading.Thread(target=_worker, args=(ll,), daemon=True).start()


def want(a, b):
    if not _started or a == b:
        return
    key = f"{a}-{b}"
    if key not in _legs and key not in _queued:
        _queued.add(key)
        _wanted.put((a, b))


def path(a, b):
    """Street points from a to b, or None if not fetched yet."""
    return _legs.get(f"{a}-{b}") if _started else None


def along(pts, f):
    """The point a fraction f of the way along a polyline, by distance."""
    if f <= 0:
        return tuple(pts[0])
    if f >= 1:
        return tuple(pts[-1])
    seg = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    total = sum(seg) or 1
    goal, run = f * total, 0.0
    for i, d in enumerate(seg):
        if run + d >= goal:
            k = (goal - run) / (d or 1)
            return (pts[i][0] + (pts[i + 1][0] - pts[i][0]) * k, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * k)
        run += d
    return tuple(pts[-1])


def rest(pts, f):
    """The polyline from fraction f to the end (the part of a leg still to drive)."""
    if not pts:
        return []
    here = along(pts, f)
    seg = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    total = sum(seg) or 1
    goal, run = f * total, 0.0
    for i, d in enumerate(seg):
        if run + d >= goal:
            return [list(here)] + [list(p) for p in pts[i + 1:]]
        run += d
    return [list(pts[-1])]
