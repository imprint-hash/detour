"""Route maths. The agent decides *what* should happen; this works out the order.

Given the vans that can still drive, where each one is and when it is free, and
the stops still to deliver, it returns a sequence per van. Lateness is allowed
but costs per minute, and it costs more for a pharmacy than for a house, so the
plan sacrifices the right deliveries when the day goes wrong.
"""
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

LATE_COST_PER_MIN = {"pharmacy": 60, "clinic": 50, "restaurant": 25, "home": 4}
DROP_COST = 10_000_000  # only if a stop truly cannot fit
MOVE_COST = 60          # handing a stop to a different driver; about 15 min late for a house


class NoSolution(Exception):
    """The solver ran out of time or found nothing feasible."""


def plan(day, vans, todo, *, travel, now_min, extra_priority=(), earliest=None, current=None, seconds=3, max_stops=24, polish=True):
    """
    vans:  [{"id", "at": node, "free_min": minute it can leave}]   (node 0 = depot)
    todo:  [order index (1-based node)]                            stops left
    travel(a, b) -> seconds between nodes, with any closures applied
    extra_priority: nodes the agent marked urgent (treated like a pharmacy)
    earliest: {node: minute} it cannot be delivered before (parcels in transit, customer asked)
    current: {node: van_id} who holds the stop now; moving it to another van costs MOVE_COST.
             Stops whose van is not in `vans` (a broken one) have no such cost.
    Returns {van_id: [node, ...]} and a list of dropped nodes.
    """
    if not vans or not todo:
        return {v["id"]: [] for v in vans}, list(todo)
    nodes = [0] + sorted(set(todo)) + sorted({v["at"] for v in vans} - {0} - set(todo))
    pos = {n: i for i, n in enumerate(nodes)}
    starts = [pos[v["at"]] for v in vans]
    ends = [0] * len(vans)
    mgr = pywrapcp.RoutingIndexManager(len(nodes), len(vans), starts, ends)
    rt = pywrapcp.RoutingModel(mgr)
    van_ids = {v["id"] for v in vans}
    orders = day["orders"]

    def svc(n):
        return 0 if n == 0 else orders[n - 1]["service_min"]

    def cb(i, j):
        a, b = nodes[mgr.IndexToNode(i)], nodes[mgr.IndexToNode(j)]
        return round(travel(a, b) / 60) + svc(a)

    t = rt.RegisterTransitCallback(cb)
    current = current or {}
    for k, v in enumerate(vans):
        def cost(i, j, vid=v["id"]):
            b = nodes[mgr.IndexToNode(j)]
            moved = current.get(b) not in (None, vid) and current.get(b) in van_ids
            return cb(i, j) + (MOVE_COST if moved else 0)
        rt.SetArcCostEvaluatorOfVehicle(rt.RegisterTransitCallback(cost), k)
    horizon = 24 * 60
    rt.AddDimension(t, 120, horizon, False, "time")
    # A driver can only carry so much: cap the stops per van.
    one = rt.RegisterUnaryTransitCallback(lambda i: 0 if nodes[mgr.IndexToNode(i)] == 0 else 1)
    rt.AddDimensionWithVehicleCapacity(one, 0, [max_stops] * len(vans), True, "stops")
    dim = rt.GetDimensionOrDie("time")
    # Spread the work: a van finishing at 4 pm while another idles is a bad plan.
    dim.SetGlobalSpanCostCoefficient(40)
    for k, v in enumerate(vans):
        dim.CumulVar(rt.Start(k)).SetRange(max(int(v["free_min"]), now_min), horizon)
    for n in todo:
        idx = mgr.NodeToIndex(pos[n])
        o = orders[n - 1]
        kind = "pharmacy" if n in extra_priority else o["kind"]
        dim.SetCumulVarSoftUpperBound(idx, int(o["promise_min"]), LATE_COST_PER_MIN[kind])
        if earliest and n in earliest:
            dim.CumulVar(idx).SetMin(int(earliest[n]))
        rt.AddDisjunction([idx], DROP_COST)

    p = pywrapcp.DefaultRoutingSearchParameters()
    p.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    if polish:
        p.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    p.time_limit.seconds = seconds
    sol = rt.SolveWithParameters(p)
    if not sol:
        raise NoSolution(f"no route found in {seconds}s")

    out, seen = {}, set()
    for k, v in enumerate(vans):
        i, seq = rt.Start(k), []
        while not rt.IsEnd(i):
            n = nodes[mgr.IndexToNode(i)]
            if n in todo:
                seq.append(n)
                seen.add(n)
            i = sol.Value(rt.NextVar(i))
        out[v["id"]] = seq
    return out, [n for n in todo if n not in seen]


def greedy(day, vans, todo, *, travel, current=None):
    """Last-resort plan with no solver: every van keeps the stops it has, and each
    stop nobody holds goes where it adds the least driving. Always answers."""
    current = current or {}
    ids = {v["id"] for v in vans}
    routes = {v["id"]: [n for n in todo if current.get(n) == v["id"]] for v in vans}
    # Keep each van's current order.
    for v in vans:
        routes[v["id"]].sort(key=lambda n: (current_order(current, v["id"], n)))
    free = [n for n in todo if current.get(n) not in ids]
    at = {v["id"]: v["at"] for v in vans}
    for n in free:
        best = None
        for vid, seq in routes.items():
            path = [at[vid]] + seq
            for i in range(len(seq) + 1):
                a = path[i]
                b = path[i + 1] if i < len(seq) else None
                add = travel(a, n) + (travel(n, b) - travel(a, b) if b is not None else 0)
                add += 60 * len(seq)  # spread the work: a longer list costs a minute per stop
                if best is None or add < best[0]:
                    best = (add, vid, i)
        _, vid, i = best
        routes[vid].insert(i, n)
    return routes, []


def current_order(current, vid, n):
    return list(current).index(n)  # `current` is built in plan order, so dict order is route order
