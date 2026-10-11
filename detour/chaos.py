"""The chaotic day. Every event changes the real world first, then drops a message
in the dispatcher's inbox, written the way people actually write when things go
wrong. Some events fix themselves only if the agent reads them properly (an
urgent order, a customer who will be out); others (a broken van, a closed road)
hurt whether or not anyone reacts, and the agent's job is to limit the damage.

The same seed always produces the same day, so a fixed agent can be compared
with the old one on identical chaos.
"""
import random
from .world import hhmm


def _pick(world, rng, kinds=None, on_van=None, pending=True, ok=None):
    """A target for an event: any order not yet delivered, whichever van holds it, so the
    same seed hits the same order whatever the dispatcher did earlier."""
    holder = {n: v["id"] for v in world.vans.values() for n in v["plan"]}
    cands = [(holder.get(n), n) for n, o in enumerate(world.orders, 1)
             if (kinds is None or o["kind"] in kinds) and (not pending or n not in world.delivered)
             and (not on_van or holder.get(n) == on_van) and (ok is None or ok(n))]
    return rng.choice(cands) if cands else (None, None)


class Chaos:
    """Scripted events. Times are minutes after midnight; targets are chosen when they fire."""

    def __init__(self, seed=7):
        self.rng = random.Random(seed)
        self.script = [
            (9 * 60 + 40, self.traffic),
            (9 * 60 + 50, self.urgent),
            (10 * 60 + 15, self.breakdown),
            (10 * 60 + 30, self.closure),
            (10 * 60 + 50, self.not_home),
            (11 * 60 + 20, self.locked),
        ]
        self.fired = set()
        self.manual = []  # Chaos Button presses waiting to fire

    def due(self, world):
        out = []
        for i, (t, fn) in enumerate(self.script):
            if i not in self.fired and world.clock >= t:
                self.fired.add(i)
                msg = fn(world)
                if msg:
                    out.append(msg)
        while self.manual:
            msg = getattr(self, self.manual.pop(0))(world)
            if msg:
                out.append(msg)
        return out

    def _msg(self, world, frm, text, **meta):
        m = {"id": f"M{len(world.inbox) + 1}", "t": hhmm(world.clock), "from": frm, "text": text, "handled": False, **meta}
        world.inbox.append(m)
        world.events.append({"t": world.clock, "kind": "message", "from": frm, "text": text})
        return m

    # ---- the events ----
    def traffic(self, world):
        vans = [v for v in world.vans.values() if v["status"] == "driving"]
        if not vans:
            return None
        v = self.rng.choice(vans)
        world.delay_van(v["id"], 25)
        return self._msg(world, f"driver {v['name']}", "stuck behind a crash on the 60, barely moving. prob 25 min behind", van=v["id"])

    def urgent(self, world):
        # The warehouse only asks for what a van could still manage: pick a medicine stop
        # that the nearest working van can reach with ten minutes to spare.
        deadline = 10 * 60 + 45

        def reachable(n):
            vans = world.available_vans()
            return any(max(world.clock, v["free_min"]) + world.travel(v["at"], n) / 60 <= deadline - 10 for v in vans)

        for kinds in ({"pharmacy"}, {"clinic"}):
            vid, n = _pick(world, self.rng, kinds=kinds, ok=reachable)
            if n:
                break
        if not n:
            return None
        o = world.orders[n - 1]
        return self._msg(world, "warehouse",
                         f"URGENT — order {o['id']} for {o['name']} ({o['street']}) has insulin for a patient. "
                         f"The {o['kind']} says it must be there by 10:45 latest.", order=o["id"])

    def breakdown(self, world):
        live = [v for v in world.vans.values() if v["status"] in ("driving", "serving", "idle") and len(v["plan"]) >= 4]
        if not live:
            return None
        v = max(live, key=lambda v: len(v["plan"]))
        street = world.orders[(v["next"] or v["at"] or 1) - 1]["street"] or "the road"
        world.break_van(v["id"])
        return self._msg(world, f"driver {v['name']}",
                         f"van's making a horrible grinding noise, pulled over on {street}. not safe to drive. what do i do with the parcels",
                         van=v["id"])

    def closure(self, world):
        pend = world.pending()
        if not pend:
            return None
        # Close the road where pending stops are densest.
        from .world import km
        centre = max(pend, key=lambda n: sum(1 for m in pend if km(world.ll(n), world.ll(m)) < 1.5))
        lat, lng = world.ll(centre)
        world.close_area(lat, lng, 1.5, 3.0, 2)
        street = world.orders[centre - 1]["street"] or "the area"
        return self._msg(world, "city traffic alert",
                         f"Police activity: roads around {street} closed / heavy diversions for ~2 hours. Avoid if possible.",
                         near_order=world.orders[centre - 1]["id"])

    def not_home(self, world):
        vid, n = _pick(world, self.rng, kinds={"home"})
        if not n:
            return None
        o = world.orders[n - 1]
        return self._msg(world, f"customer ({o['id']})",
                         "hi, I won't be home till 2pm today, can you bring my package after 2 please? thanks", order=o["id"])

    def locked(self, world):
        busy = [v for v in world.vans.values() if v["status"] == "driving" and v["next"]]
        if not busy:
            return None
        v = self.rng.choice(busy)
        o = world.orders[v["next"] - 1]
        return self._msg(world, f"driver {v['name']}",
                         f"at {o['id']} ({o['street']}) gate's locked and nobody answering. leave it or come back?",
                         van=v["id"], order=o["id"])

    # ---- the Chaos Button ----
    def press(self, kind):
        if kind in ("breakdown", "closure", "urgent", "traffic", "not_home", "locked"):
            self.manual.append(kind)
