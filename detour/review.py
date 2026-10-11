"""Reads a finished run's decision log and lists the dispatcher's own mistakes.

The same tool calls are traced in neatlogs; this is the checklist we apply to them:
wrong ids, missed replans, driver spam, ignored requests.

    python -m detour.review runs/v2-s7-abc123.json
"""
import json, sys
from collections import defaultdict

CHANGES_PLAN = {"set_priority", "set_customer_window", "retry_later"}
CATEGORIES = ["wrong_id", "missed_replan", "driver_spam", "ignored_request", "tool_error"]


def review(run):
    turns = defaultdict(list)
    handled_msgs = {}
    for d in run["decisions"]:
        if d["tool"] == "<messages>":
            handled_msgs[d["turn"]] = d["messages"]
        else:
            turns[d["turn"]].append(d)
    inbox = {m["id"]: m for m in run["inbox"]}
    found = []

    def flag(cat, turn, text):
        found.append({"category": cat, "turn": turn, "t": next((d["t"] for d in turns[turn]), "?"), "text": text})

    for turn, calls in turns.items():
        names = [c["tool"] for c in calls]
        for c in calls:
            err = "error" in c["result"] or '"ok": false' in c["result"]
            if err and any(k in c["result"] for k in ("no order", "no van", "no message")):
                flag("wrong_id", turn, f"{c['tool']}({json.dumps(c['args'])}) -> {c['result'][:90]}")
            elif err and c["tool"] == "message_driver":
                flag("driver_spam", turn, f"message to {c['args'].get('van_id')} refused: no stop changed")
            elif err:
                flag("tool_error", turn, f"{c['tool']} -> {c['result'][:90]}")
            if c["tool"] == "message_driver" and "check your plan" in str(c["args"].get("text", "")).lower():
                flag("driver_spam", turn, f"generic message to {c['args'].get('van_id')}: 'check your plan'")
        sent = [c["args"].get("van_id") for c in calls if c["tool"] == "message_driver" and c["ok"]]
        for v in {v for v in sent if sent.count(v) > 1}:
            flag("driver_spam", turn, f"{v} was messaged {sent.count(v)} times in one turn")
        for i, c in enumerate(calls):
            if c["tool"] in CHANGES_PLAN and c["ok"] and "replan" not in names[i + 1:]:
                flag("missed_replan", turn, f"{c['tool']}({c['args'].get('order_id')}) was never followed by replan")
        # Each message must get the action it calls for.
        for mid in handled_msgs.get(turn, []):
            m = inbox[mid]
            text, did = m["text"].lower(), [c for c in calls if c["ok"]]
            want = None
            if m["from"] == "warehouse" and m.get("order"):
                want = ("set_priority", m["order"])
            elif m["from"].startswith("customer") and m.get("order"):
                want = ("set_customer_window", m["order"])
            elif m["from"].startswith("driver") and m.get("order") and "locked" in text:
                want = ("retry_later", m["order"])
            elif m["from"].startswith("driver") and "not safe to drive" in text:
                want = ("replan", None)
            elif m["from"] == "city traffic alert":
                want = ("replan", None)
            if want and not any(c["tool"] == want[0] and (want[1] is None or c["args"].get("order_id") == want[1]) for c in did):
                flag("ignored_request", turn, f"{mid} ({m['from']}) needed {want[0]}" + (f" for {want[1]}" if want[1] else "") + ", not done")
            if want and want[0] != "replan" and "replan" not in names:
                flag("missed_replan", turn, f"{mid}: {want[0]} done but no replan")
            if want and want[0] == "replan" and "replan" not in names:
                flag("missed_replan", turn, f"{mid}: {m['from']} needed a replan, none made")
    for m in run["inbox"]:
        if not m["handled"]:
            flag("ignored_request", None, f"{m['id']} ({m['from']}) was never marked handled")
    # An urgent order must be given the warehouse's deadline, not a made-up one.
    for d in run["decisions"]:
        if d["tool"] == "set_priority" and d["ok"] and d["args"].get("deadline") != "10:45":
            flag("wrong_id", d["turn"], f"set_priority deadline {d['args'].get('deadline')} (warehouse said 10:45)")
    return found


def counts(found):
    return {c: sum(1 for f in found if f["category"] == c) for c in CATEGORIES}


if __name__ == "__main__":
    for path in sys.argv[1:]:
        run = json.load(open(path))
        f = review(run)
        print(path, counts(f))
        for x in f:
            print(f"  [{x['category']}] {x['t']} {x['text']}")
