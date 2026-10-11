"""The delivery day: vans moving on real road times, and the chaos that hits them.

Time is in minutes after midnight, local. The clock only moves when step() is
called, so the agent can stop the world while it thinks, the way a dispatcher
would put the phone down and look at the map.
"""
import json, math
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
HANDOFF_MIN = 25  # a rescue van needs this long to collect a broken van's parcels


def hhmm(m):
    m = int(round(m))
    return f"{m // 60:02d}:{m % 60:02d}"


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


class World:
    def __init__(self, day=None):
        self.day = day or json.loads((DATA / "day.json").read_text())
        self.M = self.day["matrix_s"]
        self.orders = self.day["orders"]
        self.clock = float(self.day["start_min"])
        self.vans = {
            v["id"]: {"id": v["id"], "name": v["name"], "at": 0, "next": None, "depart": None, "arrive": None,
                      "plan": [], "status": "idle", "free_min": self.clock, "done": []}
            for v in self.day["vans"]
        }
        self.delivered = {}        # node -> minute
        self.earliest = {}         # node -> minute it may be delivered (customer asked, or parcels in transit)
        self.priority = set()      # nodes the agent marked urgent
        self.closures = []         # {"lat","lng","km","factor","until"}
        self.inbox = []            # messages from drivers, customers, the warehouse, the city
        self.refunds = {}          # node -> {"amount", "status", "id"}
        self.events = []           # human-readable timeline for the screen
        self.messages_out = []     # what the agent told drivers

    # ---------- geography ----------
    def ll(self, n):
        if n == 0:
            return (self.day["depot"]["lat"], self.day["depot"]["lng"])
        o = self.orders[n - 1]
        return (o["lat"], o["lng"])

    def travel(self, a, b):
        s = self.M[a][b]
        for c in self.closures:
            if c["until"] > self.clock and (km(self.ll(a), (c["lat"], c["lng"])) < c["km"] or km(self.ll(b), (c["lat"], c["lng"])) < c["km"]):
                s *= c["factor"]
        return s

    def van_pos(self, v):
        if v["status"] == "driving" and v["next"] is not None:
            a, b = self.ll(v["at"]), self.ll(v["next"])
            f = min(1, max(0, (self.clock - v["depart"]) / max(0.01, v["arrive"] - v["depart"])))
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
        return self.ll(v["at"])

    def nearest_node(self, lat, lng):
        return min(range(len(self.M)), key=lambda n: km(self.ll(n), (lat, lng)))

    # ---------- the clock ----------
    def step(self, dt=1.0):
        end = self.clock + dt
        while True:
            soonest = None
            for v in self.vans.values():
                if v["status"] == "driving" and v["arrive"] <= end:
                    soonest = v["arrive"] if soonest is None else min(soonest, v["arrive"])
                elif v["status"] == "serving" and v["free_min"] <= end:
                    soonest = v["free_min"] if soonest is None else min(soonest, v["free_min"])
                elif v["status"] == "idle" and v["plan"] and max(v["free_min"], self.clock) <= end:
                    soonest = max(v["free_min"], self.clock) if soonest is None else min(soonest, max(v["free_min"], self.clock))
            if soonest is None:
                break
            self.clock = max(self.clock, soonest)
            for v in self.vans.values():
                self._advance(v)
        self.clock = end

    def _advance(self, v):
        if v["status"] == "driving" and v["arrive"] <= self.clock:
            n = v["next"]
            start = max(v["arrive"], self.earliest.get(n, 0))
            v["at"], v["next"], v["status"] = n, None, "serving"
            v["free_min"] = start + self.orders[n - 1]["service_min"]
            self.delivered[n] = start
            v["done"].append(n)
            o = self.orders[n - 1]
            late = start - self.promised(n)
            self.events.append({"t": start, "kind": "delivered", "van": v["id"], "order": o["id"],
                                "late_min": round(late) if late > 0 else 0})
        if v["status"] == "serving" and v["free_min"] <= self.clock:
            v["status"] = "idle"
        if v["status"] == "idle" and v["plan"] and v["free_min"] <= self.clock:
            n = v["plan"].pop(0)
            v["next"], v["status"] = n, "driving"
            v["depart"] = max(self.clock, v["free_min"])
            v["arrive"] = v["depart"] + self.travel(v["at"], n) / 60

    # ---------- what the agent can change ----------
    def pending(self):
        """Stops not yet delivered, and not on their way inside a van that is driving to them."""
        en_route = {v["next"] for v in self.vans.values() if v["status"] == "driving" and v["next"]}
        return [n for n in range(1, len(self.orders) + 1) if n not in self.delivered and n not in en_route]

    def available_vans(self):
        out = []
        for v in self.vans.values():
            if v["status"] == "broken":
                continue
            if v["status"] == "driving":
                out.append({"id": v["id"], "at": v["next"], "free_min": v["arrive"] + self.orders[v["next"] - 1]["service_min"]})
            else:
                out.append({"id": v["id"], "at": v["at"], "free_min": max(self.clock, v["free_min"])})
        return out

    def assign(self, routes):
        owner = {n: vid for vid, v in self.vans.items() for n in v["plan"]}
        moved = 0
        for vid, seq in routes.items():
            moved += sum(1 for n in seq if n in owner and owner[n] != vid)
            self.vans[vid]["plan"] = list(seq)
        # Stops handed from one driver to another: drivers notice every one.
        self.events.append({"t": self.clock, "kind": "assign", "moved": moved})
        return moved

    def break_van(self, vid):
        """The van stops where it is. Its parcels need a rescue before anyone else can deliver them."""
        v = self.vans[vid]
        stranded = ([v["next"]] if v["next"] else []) + v["plan"]
        if v["status"] == "driving":
            # Snap the stopped van to the nearest known point so the map and maths agree.
            v["at"] = self.nearest_node(*self.van_pos(v))
        v["status"], v["next"], v["plan"] = "broken", None, []
        for n in stranded:
            self.earliest[n] = max(self.earliest.get(n, 0), self.clock + HANDOFF_MIN)
        return stranded

    def delay_van(self, vid, minutes):
        v = self.vans[vid]
        if v["status"] == "driving":
            v["arrive"] += minutes
        else:
            v["free_min"] = max(v["free_min"], self.clock) + minutes

    def close_area(self, lat, lng, radius_km, factor, hours):
        self.closures.append({"lat": lat, "lng": lng, "km": radius_km, "factor": factor, "until": self.clock + hours * 60})

    # ---------- scoring ----------
    def promised(self, n):
        """When order n is due. A customer who asked for later gets the hour they asked for."""
        o = self.orders[n - 1]
        return self.earliest[n] + 60 if o.get("customer_window") and n in self.earliest else o["promise_min"]

    def score(self):
        rows = []
        for n, t in self.delivered.items():
            o = self.orders[n - 1]
            rows.append(t - self.promised(n))
        on_time = sum(1 for r in rows if r <= 0)
        return {"delivered": len(rows), "on_time": on_time, "late": len(rows) - on_time,
                "total": len(self.orders), "refunds_usd": round(sum(r["amount"] for r in self.refunds.values()), 2)}

    def snapshot(self):
        return {
            "clock": hhmm(self.clock),
            "vans": [{"id": v["id"], "name": v["name"], "status": v["status"], "pos": self.van_pos(v),
                      "plan": [self.orders[n - 1]["id"] for n in v["plan"]], "done": len(v["done"])}
                     for v in self.vans.values()],
            "score": self.score(),
            "closures": [c for c in self.closures if c["until"] > self.clock],
        }
