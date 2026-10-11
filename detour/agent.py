"""Detour's dispatcher: reads what people send, decides, re-plans, tells drivers, checks.

The model never moves a van or a dollar directly. It calls tools; the tools act on
the world and say what happened. Every call is traced in neatlogs.
"""
import json, os
from openai import OpenAI
import neatlogs
from neatlogs.decorators import span

from .solver import plan
from .world import hhmm
from . import payments

AUTO_REFUND_LIMIT = 10.00   # above this, a person approves
GOODWILL = 25.00            # credit for a business whose delivery was badly late

SYSTEM = """You are Detour, the dispatcher for a same-morning delivery fleet out of the Amazon DLA7 depot in Chino, California.
Messages arrive from drivers, customers, the warehouse and the city. For each one, work out what really changed and act with your tools.

Rules:
- Read the inbox, then fix things. A message only counts as handled once you have acted on it and called mark_handled.
- If something changes what the vans should do (a van out of action, a closed road, an urgent order, a customer asking for a later time), call replan once you have recorded the change, and check the forecast it returns.
- Tell only the drivers listed in replan's changed_vans, one short plain message each. Say what changed, using what_changed (e.g. "+3 stops: O012, O019, O044"). Never send "check your plan".
- Urgent medical deliveries come first: use set_priority with the deadline you were given.
- A customer who asks for a later time gets it: use set_customer_window. That is not a late delivery.
- Never invent an order id, van id or time. Use get_fleet to look things up.
- Be brief. You are on the clock."""

TOOLS = [
    {"name": "get_fleet", "description": "Every van: status, where it is going next, its remaining stops, and the forecast of late deliveries.", "parameters": {"type": "object", "properties": {}}},
    {"name": "read_inbox", "description": "Unhandled messages from drivers, customers, the warehouse and the city.", "parameters": {"type": "object", "properties": {}}},
    {"name": "mark_handled", "description": "Mark a message handled once you have acted on it.", "parameters": {"type": "object", "properties": {"message_id": {"type": "string"}, "summary": {"type": "string", "description": "one line: what you did"}}, "required": ["message_id", "summary"]}},
    {"name": "set_priority", "description": "Make an order urgent with a new deadline (HH:MM).", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}, "deadline": {"type": "string"}}, "required": ["order_id", "deadline"]}},
    {"name": "set_customer_window", "description": "The customer asked not to be delivered before this time (HH:MM).", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}, "not_before": {"type": "string"}}, "required": ["order_id", "not_before"]}},
    {"name": "retry_later", "description": "A delivery could not be made (locked gate, no answer). Try again no earlier than this many minutes from now.", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}, "minutes": {"type": "integer"}}, "required": ["order_id", "minutes"]}},
    {"name": "replan", "description": "Re-plan every remaining stop across the vans that can still drive, avoiding closed roads and respecting priorities and customer windows. Returns the forecast.", "parameters": {"type": "object", "properties": {}}},
    {"name": "message_driver", "description": "Send a short message to a driver.", "parameters": {"type": "object", "properties": {"van_id": {"type": "string"}, "text": {"type": "string"}}, "required": ["van_id", "text"]}},
]


def _parse_hhmm(s):
    h, m = str(s).strip().split(":")[:2]
    return int(h) * 60 + int(m)


class Dispatcher:
    def __init__(self, world, *, model=None, base_url=None, api_key=None, version="v1"):
        self.w = world
        self.version = version
        self.client = OpenAI(base_url=base_url or os.environ.get("LLM_BASE_URL"), api_key=api_key or os.environ.get("LLM_API_KEY"))
        self.model = model or os.environ.get("LLM_MODEL", "openai/gpt-oss-120b")
        self.forecast = None
        self._last_key, self._last_result = None, None
        self.changes = {}   # van -> what changed in the last replan

    # ---------- tools ----------
    def _order_node(self, oid):
        for i, o in enumerate(self.w.orders):
            if o["id"] == oid:
                return i + 1
        raise ValueError(f"no order {oid}")

    @span(kind="TOOL", tool_name="get_fleet")
    def get_fleet(self):
        w = self.w
        vans = []
        for v in w.vans.values():
            nxt = w.orders[v["next"] - 1]["id"] if v["next"] else None
            vans.append({"van": v["id"], "status": v["status"], "next_stop": nxt,
                         "remaining": [w.orders[n - 1]["id"] for n in v["plan"]], "delivered": len(v["done"])})
        return {"clock": hhmm(w.clock), "vans": vans, "forecast": self.forecast}

    @span(kind="TOOL", tool_name="read_inbox")
    def read_inbox(self):
        return [m for m in self.w.inbox if not m["handled"]]

    @span(kind="TOOL", tool_name="mark_handled")
    def mark_handled(self, message_id, summary):
        for m in self.w.inbox:
            if m["id"] == message_id:
                m["handled"], m["summary"] = True, summary
                self.w.events.append({"t": self.w.clock, "kind": "handled", "message": message_id, "text": summary})
                return {"ok": True}
        return {"ok": False, "error": f"no message {message_id}"}

    @span(kind="TOOL", tool_name="set_priority")
    def set_priority(self, order_id, deadline):
        n = self._order_node(order_id)
        self.w.priority.add(n)
        self.w.orders[n - 1]["promise_min"] = min(self.w.orders[n - 1]["promise_min"], _parse_hhmm(deadline))
        return {"ok": True, "order": order_id, "deadline": deadline, "note": "call replan to act on it"}

    @span(kind="TOOL", tool_name="set_customer_window")
    def set_customer_window(self, order_id, not_before):
        n = self._order_node(order_id)
        self.w.earliest[n] = max(self.w.earliest.get(n, 0), _parse_hhmm(not_before))
        o = self.w.orders[n - 1]
        o["customer_window"] = True
        o["promise_min"] = self.w.earliest[n] + 60  # the plan aims for the hour they asked for
        return {"ok": True, "order": order_id, "not_before": not_before, "note": "call replan to act on it"}

    @span(kind="TOOL", tool_name="retry_later")
    def retry_later(self, order_id, minutes):
        n = self._order_node(order_id)
        # The van that was driving there skips it; the stop goes back into the pool.
        for v in self.w.vans.values():
            if v["next"] == n and v["status"] == "driving":
                v["next"], v["status"] = None, "idle"
                v["free_min"] = max(v["free_min"], v["arrive"])
                v["at"] = n
        self.w.earliest[n] = self.w.clock + int(minutes)
        return {"ok": True, "order": order_id, "retry_after": hhmm(self.w.clock + int(minutes)), "note": "call replan"}

    @span(kind="TOOL", tool_name="replan")
    def replan(self):
        w = self.w
        # Same situation as the last replan (the model asked twice): hand back the same answer.
        key = (round(w.clock), tuple(w.pending()), frozenset(w.priority), tuple(sorted(w.earliest.items())),
               len(w.closures), tuple((v["id"], v["at"], v["free_min"]) for v in w.available_vans()),
               tuple(tuple(v["plan"]) for v in w.vans.values()))
        if self.version != "v1" and key == self._last_key:
            return {**self._last_result, "note": "nothing has changed since the last replan"}
        before = {vid: list(v["plan"]) for vid, v in w.vans.items()}
        current = {n: vid for vid, seq in before.items() for n in seq}
        routes, dropped = plan(w.day, w.available_vans(), w.pending(), travel=w.travel, now_min=int(w.clock),
                               extra_priority=w.priority, earliest=w.earliest, current=current, seconds=3)
        w.assign(routes)
        self.forecast = self._forecast()
        changed = [vid for vid in routes if set(routes[vid]) != set(before.get(vid, []))]
        self.changes = {vid: self._describe(before.get(vid, []), routes[vid]) for vid in changed}
        w.events.append({"t": w.clock, "kind": "replan", "changed": changed, "forecast": self.forecast})
        result = {"changed_vans": changed, "what_changed": self.changes,
                  "dropped": [w.orders[n - 1]["id"] for n in dropped], "forecast": self.forecast}
        self._last_key = key[:6] + (tuple(tuple(v["plan"]) for v in w.vans.values()),)
        self._last_result = {**result, "changed_vans": [], "what_changed": {}}
        return result

    def _describe(self, old, new):
        ids = lambda ns: ", ".join(self.w.orders[n - 1]["id"] for n in ns)
        added, removed = [n for n in new if n not in old], [n for n in old if n not in new]
        parts = ([f"+{len(added)} stop{'s' * (len(added) != 1)}: {ids(added)}"] if added else []) \
            + ([f"-{len(removed)} stop{'s' * (len(removed) != 1)}: {ids(removed)}"] if removed else [])
        return "; ".join(parts)

    @span(kind="TOOL", tool_name="message_driver")
    def message_driver(self, van_id, text):
        if van_id not in self.w.vans:
            return {"ok": False, "error": f"no van {van_id}"}
        if self.version != "v1" and van_id not in self.changes and self.w.vans[van_id]["status"] != "broken":
            return {"ok": False, "error": f"{van_id}'s stops did not change in the last replan, so no message was sent"}
        self.w.messages_out.append({"t": hhmm(self.w.clock), "van": van_id, "text": text})
        self.w.events.append({"t": self.w.clock, "kind": "to_driver", "van": van_id, "text": text})
        return {"ok": True}

    def _forecast(self):
        """Walk every plan forward on current road times and count who will be late."""
        w, late = self.w, []
        for v in w.available_vans():
            clock, at = v["free_min"], v["at"]
            for n in w.vans[v["id"]]["plan"]:
                clock = max(clock + w.travel(at, n) / 60, w.earliest.get(n, 0))
                o = w.orders[n - 1]
                promised = w.promised(n)
                if clock > promised:
                    late.append({"order": o["id"], "kind": o["kind"], "late_min": round(clock - promised)})
                clock += o["service_min"]
                at = n
        return {"late_count": len(late), "late": sorted(late, key=lambda x: -x["late_min"])[:8]}

    # ---------- the loop ----------
    def _call(self, name, args):
        fn = getattr(self, name, None)
        if not fn or name not in {t["name"] for t in TOOLS}:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(**args)
        except Exception as e:
            return {"error": str(e)}

    @span(kind="AGENT", role="Dispatcher", goal="Keep every delivery on time through the chaos")
    def handle(self, new_messages, max_rounds=12):
        msgs = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"It is {hhmm(self.w.clock)}. New messages just arrived: "
                                            + json.dumps([{k: m[k] for k in ('id', 'from', 'text')} for m in new_messages])}]
        tools = [{"type": "function", "function": t} for t in TOOLS]
        for _ in range(max_rounds):
            r = self.client.chat.completions.create(model=self.model, messages=msgs, tools=tools, temperature=0.2)
            m = r.choices[0].message
            msgs.append({"role": "assistant", "content": m.content or "", "tool_calls": [tc.model_dump() for tc in (m.tool_calls or [])]} if m.tool_calls else {"role": "assistant", "content": m.content or ""})
            if not m.tool_calls:
                break
            for tc in m.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                out = self._call(tc.function.name, args)
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(out, default=str)[:6000]})
        return m.content

    @span(kind="CHAIN", name="close_out_late_deliveries")
    def close_out(self, approve=lambda req: True):
        """End of day: confirm each late delivery really was late, then refund it once."""
        w = self.w
        for n, t in sorted(w.delivered.items()):
            o = w.orders[n - 1]
            late = t - w.promised(n)
            if late <= 0 or n in w.refunds:
                continue
            amount = o["fee"] + (GOODWILL if o["kind"] != "home" and late > 30 else 0)
            req = {"order": o["id"], "kind": o["kind"], "late_min": round(late), "amount": round(amount, 2)}
            status = "auto"
            if amount > AUTO_REFUND_LIMIT:
                status = "approved" if approve(req) else "declined"
                if status == "declined":
                    w.refunds[n] = {"amount": 0, "status": "declined", **req}
                    continue
            w.refunds[n] = {**payments.refund(o, amount, reason=f"{round(late)} min late"), "status": status, **req}
        return w.refunds
