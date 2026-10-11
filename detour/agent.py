"""Detour's dispatcher: reads what people send, decides, re-plans, tells drivers, checks.

The model never moves a van or a dollar directly. It calls tools; the tools act on
the world and say what happened. Every call is traced in neatlogs.
"""
import json, os, time
import openai
from openai import OpenAI
import neatlogs
from neatlogs.decorators import span

from .solver import plan, greedy, NoSolution
from .faults import Faults
from .world import hhmm
from . import payments

AUTO_REFUND_LIMIT = 10.00   # above this, a person approves
GOODWILL = 25.00            # credit for a business whose delivery was badly late

TELL_V1 = "- Tell every driver whose route changed, in one short plain message."
TELL_V2 = ("- Tell only the drivers listed in replan's changed_vans, one short plain message each. "
           'Say what changed, using what_changed (e.g. "+3 stops: O012, O019, O044"). Never send "check your plan".')

SYSTEM = """You are Detour, the dispatcher for a same-morning delivery fleet out of the Amazon DLA7 depot in Chino, California.
Messages arrive from drivers, customers, the warehouse and the city. For each one, work out what really changed and act with your tools.

Rules:
- Read the inbox, then fix things. A message only counts as handled once you have acted on it and called mark_handled.
- If something changes what the vans should do (a van out of action, a closed road, an urgent order, a customer asking for a later time), call replan once you have recorded the change, and check the forecast it returns.
{TELL}
- Urgent medical deliveries come first: use set_priority with the deadline you were given.
- A customer who asks for a later time gets it: use set_customer_window. That is not a late delivery.
- Never invent an order id, van id or time. Use get_fleet to look things up.
- Be brief. You are on the clock."""

# What each version of the dispatcher does. v1 is the first build, kept so it can be replayed.
FEATURES = {
    "v1": {"stable": False, "window": False, "gate": False, "dedupe": False},
    "v2": {"stable": True, "window": True, "gate": True, "dedupe": True},
}

# provider -> (base url env, key env, model env, default model)
PROVIDERS = {
    "groq": ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "openai/gpt-oss-120b"),
    "litellm": ("LITELLM_BASE_URL", "LITELLM_API_KEY", "LITELLM_MODEL", "claude-sonnet-4.6"),
}

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
    def __init__(self, world, *, model=None, provider="groq", version="v2", faults=None):
        self.w = world
        self.version = version
        self.f = FEATURES.get(version, FEATURES["v2"])
        self.system = SYSTEM.replace("{TELL}", TELL_V2 if self.f["gate"] else TELL_V1)
        self.faults = faults or Faults()
        url_env, key_env, model_env, default_model = PROVIDERS[provider]
        self.client = OpenAI(base_url=os.environ.get(url_env), api_key=os.environ.get(key_env), max_retries=0)
        self.model = model or os.environ.get(model_env, default_model)
        self.usage = {"calls": 0, "prompt": 0, "completion": 0}
        self.decisions = []   # every tool call the model made, for review
        self.recoveries = []  # every time something broke and the dispatcher carried on
        self._turn = 0
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
        if self.f["window"]:
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
        if self.f["dedupe"] and key == self._last_key:
            return {**self._last_result, "note": "nothing has changed since the last replan"}
        before = {vid: list(v["plan"]) for vid, v in w.vans.items()}
        current = {n: vid for vid, seq in before.items() for n in seq}
        routes, dropped, degraded = self._solve(current if self.f["stable"] else None)
        w.assign(routes)
        self.forecast = self._forecast()
        same = (lambda a, b: set(a) == set(b)) if self.f["gate"] else (lambda a, b: a == b)
        changed = [vid for vid in routes if not same(routes[vid], before.get(vid, []))]
        self.changes = {vid: self._describe(before.get(vid, []), routes[vid]) for vid in changed}
        w.events.append({"t": w.clock, "kind": "replan", "changed": changed, "forecast": self.forecast})
        result = {"changed_vans": changed, "what_changed": self.changes,
                  "dropped": [w.orders[n - 1]["id"] for n in dropped], "forecast": self.forecast}
        if degraded:
            result["degraded"] = degraded
        self._last_key = key[:6] + (tuple(tuple(v["plan"]) for v in w.vans.values()),)
        self._last_result = {**result, "changed_vans": [], "what_changed": {}}
        return result

    def _solve(self, current):
        """Ask the solver; if it times out or breaks, step down to a simpler plan instead of giving up."""
        w = self.w
        kw = dict(travel=w.travel, now_min=int(w.clock), extra_priority=w.priority, earliest=w.earliest, current=current)
        vans, todo = w.available_vans(), w.pending()
        ladder = [("full", dict(seconds=3)), ("fast", dict(seconds=1, polish=False))]
        for attempt, (level, opts) in enumerate(ladder):
            try:
                self.faults.solver(attempt)
                routes, dropped = plan(w.day, vans, todo, **kw, **opts)
                if attempt:
                    self._recovered("solver", f"{ladder[0][0]} solve failed; used a {level} solve", w)
                    return routes, dropped, f"{level} solve (the full solver failed)"
                return routes, dropped, None
            except (TimeoutError, NoSolution) as e:
                self.recoveries.append({"t": hhmm(w.clock), "what": "solver", "error": str(e), "tried": level})
        routes, dropped = greedy(w.day, vans, todo, travel=w.travel, current=current)
        self._recovered("solver", "solver unavailable; kept current routes and added stops by cheapest insertion", w)
        return routes, dropped, "no solver: kept current routes, added stops by cheapest insertion"

    def _recovered(self, what, how, w):
        self.recoveries.append({"t": hhmm(w.clock), "what": what, "recovered": how})
        w.events.append({"t": w.clock, "kind": "recovered", "what": what, "text": how})

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
        if self.f["gate"] and van_id not in self.changes and self.w.vans[van_id]["status"] != "broken":
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
            self.faults.tool(name)
            out = fn(**args)
        except Exception as e:
            out = {"error": str(e)}
        self.decisions.append({"turn": self._turn, "t": hhmm(self.w.clock), "tool": name, "args": args,
                               "result": json.dumps(out, default=str)[:400], "ok": not (isinstance(out, dict) and ("error" in out or out.get("ok") is False))})
        return out

    def _chat(self, **kw):
        """One model call. Rate limits, dropped connections and unparseable output are retried."""
        for attempt in range(6):
            try:
                self.faults.llm()
                raw = self.client.chat.completions.with_raw_response.create(**kw)
                r = raw.parse()
                self.usage["calls"] += 1
                if r.usage:
                    self.usage["prompt"] += r.usage.prompt_tokens
                    self.usage["completion"] += r.usage.completion_tokens
                # LiteLLM reports what each call cost; other providers are free or unpriced here.
                try:
                    self.usage["usd"] = round(self.usage.get("usd", 0) + float(raw.headers.get("x-litellm-response-cost", 0)), 6)
                    spent = float(raw.headers.get("x-litellm-key-spend", 0))
                except ValueError:
                    spent = 0
                if spent > float(os.environ.get("BUDGET_STOP_USD", 1e9)):
                    raise RuntimeError(f"stopping: the shared key has spent ${spent:.2f}, over BUDGET_STOP_USD")
                return r
            except (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError, openai.BadRequestError) as e:
                if isinstance(e, openai.BadRequestError) and "parse" not in str(e).lower():
                    raise
                wait = float(getattr(getattr(e, "response", None), "headers", {}).get("retry-after", 0) or 0) or min(30, 2 ** attempt)
                self.recoveries.append({"t": hhmm(self.w.clock), "what": "llm", "error": type(e).__name__, "waited_s": wait})
                time.sleep(wait)
        raise RuntimeError("model unavailable after 6 tries")

    @span(kind="AGENT", role="Dispatcher", goal="Keep every delivery on time through the chaos")
    def handle(self, new_messages, max_rounds=12):
        self._turn += 1
        self.decisions.append({"turn": self._turn, "t": hhmm(self.w.clock), "tool": "<messages>", "messages": [m["id"] for m in new_messages]})
        msgs = [{"role": "system", "content": self.system},
                {"role": "user", "content": f"It is {hhmm(self.w.clock)}. New messages just arrived: "
                                            + json.dumps([{k: m[k] for k in ('id', 'from', 'text')} for m in new_messages])}]
        tools = [{"type": "function", "function": t} for t in TOOLS]
        for _ in range(max_rounds):
            r = self._chat(model=self.model, messages=msgs, tools=tools, temperature=0.2)
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
