"""Route maths. The agent decides *what* should happen; this works out the order.

Given the vans that can still drive, where each one is and when it is free, and
the stops still to deliver, it returns a sequence per van. Lateness is allowed
but costs per minute, and it costs more for a pharmacy than for a house, so the
plan sacrifices the right deliveries when the day goes wrong.
"""
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

LATE_COST_PER_MIN = {"pharmacy": 60, "clinic": 50, "restaurant": 25, "home": 4}
DROP_COST = 10_000_000  # only if a stop truly cannot fit


def plan(day, vans, todo, *, travel, now_min, extra_priority=(), earliest=None, seconds=3, max_stops=24):
    """
    vans:  [{"id", "at": node, "free_min": minute it can leave}]   (node 0 = depot)
    todo:  [order index (1-based node)]                            stops left
    travel(a, b) -> seconds between nodes, with any closures applied
    extra_priority: nodes the agent marked urgent (treated like a pharmacy)
    earliest: {node: minute} it cannot be delivered before (parcels in transit, customer asked)
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
    orders = day["orders"]

    def svc(n):
        return 0 if n == 0 else orders[n - 1]["service_min"]

    def cb(i, j):
        a, b = nodes[mgr.IndexToNode(i)], nodes[mgr.IndexToNode(j)]
        return round(travel(a, b) / 60) + svc(a)

    t = rt.RegisterTransitCallback(cb)
    rt.SetArcCostEvaluatorOfAllVehicles(t)
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
    p.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    p.time_limit.seconds = seconds
    sol = rt.SolveWithParameters(p)
    if not sol:
        return {v["id"]: [] for v in vans}, list(todo)

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
