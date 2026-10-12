"""Detour's business rules, checked on every dispatcher decision.

Each rule is one plain sentence a dispatch manager would say. After every batch of
messages the dispatcher writes a decision record (a `dispatcher_decision` span in
neatlogs) listing what it saw, what it did, and any rule it broke as
"RULE BROKEN: <rule>". A neatlogs detection per rule watches for that line, so the
rules run on every trace, live and headless, and trend in the neatlogs dashboard.

Manager rules come from the Correct button on the live screen: the manager's own note,
judged by the model against each decision record.
"""
import json
from .world import hhmm

RESHUFFLE_LIMIT = 10   # stops handed between drivers in one batch before a manager would object

# key -> the sentence (also the neatlogs detection's name)
RULES = {
    "driver_unchanged": "Dispatcher messaged a driver whose route did not change",
    "window_not_honoured": "Customer's requested time was not honoured",
    "urgent_not_prioritised": "Urgent medicine order was not prioritised",
    "many_reshuffled": "A replan reshuffled many stops between drivers",
    "wrong_id": "Dispatcher used an id that does not exist",
    "replan_repeated": "Dispatcher replanned more than once for one batch of messages",
    "parcel_orphaned": "A parcel was left with no van to deliver it",
}


def check(turn, w):
    """Built-in rules for one turn. `turn` is the Dispatcher's record of the batch."""
    found = []
    calls = turn["calls"]
    changed = turn["changed_vans"]          # vans whose set of stops changed in this batch
    wrote_in = {m.get("van") for m in turn["messages"]}   # a driver who asked something deserves an answer
    for c in calls:
        if c["tool"] == "message_driver" and c["ok"]:
            v = c["van"]
            if v not in changed and v not in wrote_in and w.vans[v]["status"] != "broken":
                found.append(("driver_unchanged", f"{v} was messaged but its stops did not change"))
        if not c["ok"] and any(k in c["result"] for k in ("no van", "no order", "no message")):
            found.append(("wrong_id", f"{c['tool']}({json.dumps(c['args'])}) was refused: {c['result'][:80]}"))
    if turn["moved"] > RESHUFFLE_LIMIT:
        found.append(("many_reshuffled", f"{turn['moved']} stops moved between drivers (limit {RESHUFFLE_LIMIT})"))
    real_replans = [c for c in calls if c["tool"] == "replan" and "nothing has changed" not in c["result"]]
    if len(real_replans) > 1:
        found.append(("replan_repeated", f"{len(real_replans)} replans for one batch"))
    planned = {n for v in w.vans.values() if v["status"] != "broken" for n in v["plan"]}
    orphans = [w.orders[n - 1]["id"] for n in w.pending() if n not in planned]
    if orphans:
        found.append(("parcel_orphaned", f"{', '.join(orphans[:8])} on no working van's list"))

    for m in turn["messages"]:
        oid = m.get("order")
        if not oid:
            continue
        n = next(i + 1 for i, o in enumerate(w.orders) if o["id"] == oid)
        o = w.orders[n - 1]
        if m["from"].startswith("customer"):
            asked = w.earliest.get(n) if o.get("customer_window") else None
            if asked is None:
                found.append(("window_not_honoured", f"{oid}: the customer's time was never recorded"))
            elif not asked <= o["promise_min"] <= asked + 60:
                found.append(("window_not_honoured",
                              f"{oid}: customer asked for after {hhmm(asked)}, the plan still aims for {hhmm(o['promise_min'])}"))
        if m["from"] == "warehouse":
            late = {x["order"] for x in (turn["forecast"] or {}).get("late", [])}
            if n not in w.priority:
                found.append(("urgent_not_prioritised", f"{oid} was never marked urgent"))
            elif oid in late:
                found.append(("urgent_not_prioritised", f"{oid} is marked urgent but forecast late"))
    return found


def judge(client, model, record, manager_rules):
    """Manager rules are plain English; the model reads the decision record and says which were broken."""
    if not manager_rules:
        return []
    ask = ("You check a delivery dispatcher's decision against the manager's rules. "
           "Reply with JSON only: {\"broken\": [{\"rule\": <rule number>, \"why\": <one short line>}]}. "
           "A rule applies only when the situation it describes happens in this record (a rule about breakdowns says "
           "nothing about a traffic delay). List a rule only if it applies and the record clearly shows it was broken.\n\n"
           "Rules:\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(manager_rules, 1)) + "\n\nDecision record:\n" + record)
    try:
        r = client.chat.completions.create(model=model, temperature=0, messages=[{"role": "user", "content": ask}])
        text = r.choices[0].message.content or ""
        out = json.loads(text[text.find("{"):text.rfind("}") + 1])
        picked = [(int(x["rule"]), x.get("why", "")) for x in out.get("broken", []) if str(x.get("rule")).isdigit()]
        return [(manager_rules[i - 1], why) for i, why in picked if 1 <= i <= len(manager_rules)]
    except Exception:
        return []   # a judge that fails says nothing; it never blocks the fleet

