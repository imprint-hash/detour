"""The report card for one day. Same chaos, same seed, so two versions of the
dispatcher can be compared on identical trouble.

Each check is something a real dispatch manager would hold a person to.
"""
from .world import hhmm

WINDOW_GRACE_MIN = 60  # "after 2pm" is honoured if we arrive between 2 and 3


def report(world):
    w, rows = world, {}
    s = w.score()
    rows["on_time"] = {"value": f"{s['on_time']}/{s['delivered']}", "pass": s["late"] == 0}

    # Urgent medicine: every order the warehouse flagged reached its deadline.
    urgent = []
    for m in w.inbox:
        if m["from"] == "warehouse" and m.get("order"):
            n = next(i + 1 for i, o in enumerate(w.orders) if o["id"] == m["order"])
            t = w.delivered.get(n)
            urgent.append(t is not None and t <= 10 * 60 + 45)
    rows["urgent_medicine_on_time"] = {"value": f"{sum(urgent)}/{len(urgent)}", "pass": all(urgent)}

    # A customer who asked for a later time got it, without being made to wait all afternoon.
    asked = []
    for m in w.inbox:
        if m["from"].startswith("customer") and m.get("order"):
            n = next(i + 1 for i, o in enumerate(w.orders) if o["id"] == m["order"])
            want = w.earliest.get(n) if w.orders[n - 1].get("customer_window") else None
            t = w.delivered.get(n)
            ok = want is not None and t is not None and want <= t <= want + WINDOW_GRACE_MIN
            asked.append({"order": m["order"], "asked": hhmm(want) if want else "not recorded",
                          "came": hhmm(t) if t else None, "ok": ok})
    rows["customer_requests_honoured"] = {"value": f"{sum(a['ok'] for a in asked)}/{len(asked)}", "pass": all(a["ok"] for a in asked), "detail": asked}

    # Drivers only hear from dispatch when their route actually changed.
    needed, sent, noise = 0, 0, 0
    changed_at = {}
    for e in w.events:
        if e["kind"] == "replan":
            changed_at.setdefault(round(e["t"]), set()).update(e.get("changed", []))
    for e in w.events:
        if e["kind"] == "to_driver":
            sent += 1
            t = round(e["t"])
            if e["van"] not in changed_at.get(t, set()) and w.vans[e["van"]]["status"] != "broken":
                noise += 1
    needed = sent - noise
    rows["driver_messages_needed"] = {"value": f"{needed} needed, {noise} noise", "pass": noise == 0}

    # Route stability: stops handed between drivers after the morning plan.
    assigns = [e for e in w.events if e["kind"] == "assign"]
    reshuffled = sum(e["moved"] for e in assigns[1:])
    rows["stops_reshuffled_between_drivers"] = {"value": f"{reshuffled} over {len(assigns) - 1} replans", "pass": reshuffled <= 20}

    unhandled = [m["id"] for m in w.inbox if not m["handled"]]
    rows["messages_handled"] = {"value": f"{len(w.inbox) - len(unhandled)}/{len(w.inbox)}", "pass": not unhandled}
    rows["refunds_usd"] = {"value": f"${s['refunds_usd']:.2f}", "pass": None}
    return rows


def print_report(rows, title="Report card"):
    print(title)
    for k, r in rows.items():
        mark = "  " if r["pass"] is None else ("✅" if r["pass"] else "❌")
        print(f"  {mark} {k.replace('_', ' '):30s} {r['value']}")
