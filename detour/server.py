"""The live screen: a map of the fleet, the inbox, the dispatcher's decisions, and
the Chaos Button. One day runs at a time; the clock pauses while Detour thinks.

    python -m detour.server   ->  http://localhost:8787
"""
import json, os, threading, time, uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import neatlogs
from neatlogs.decorators import span

from .world import World, hhmm
from .chaos import Chaos
from .agent import Dispatcher

WEB = Path(__file__).resolve().parent.parent / "web"
TICK_S = float(os.environ.get("DETOUR_TICK_S", "0.25"))  # real seconds per simulated minute


class Live:
    def __init__(self):
        self.lock = threading.Lock()
        self.w = None
        self.chaos = None
        self.d = None
        self.thinking = False
        self.running = False
        self.approvals = {}      # id -> {"req", "decision"}
        self.version = os.environ.get("DETOUR_VERSION", "v1")

    def start(self, seed=7, scripted=True):
        if self.running:
            return False
        self.w = World()
        self.chaos = Chaos(seed)
        if not scripted:
            self.chaos.script = []
        self.d = Dispatcher(self.w, version=self.version)
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _approve(self, req):
        aid = uuid.uuid4().hex[:6]
        self.approvals[aid] = {"id": aid, "req": req, "decision": None}
        for _ in range(600):  # wait up to ~2 minutes for a person
            if self.approvals[aid]["decision"] is not None:
                return self.approvals[aid]["decision"]
            time.sleep(0.2)
        self.approvals[aid]["decision"] = False
        return False

    @span(kind="WORKFLOW", name="detour_live_day")
    def _run(self):
        w, d = self.w, self.d
        self.thinking = True
        d.replan()
        self.thinking = False
        while self.running and w.clock < 16 * 60 and len(w.delivered) < len(w.orders):
            new = self.chaos.due(w)
            if new:
                self.thinking = True
                try:
                    d.handle(new)
                finally:
                    self.thinking = False
            with self.lock:
                w.step(1)
            time.sleep(TICK_S)
        self.thinking = True
        d.close_out(approve=self._approve)
        self.thinking = False
        self.running = False
        neatlogs.flush()

    def state(self):
        if not self.w:
            return {"idle": True}
        w = self.w
        with self.lock:
            snap = w.snapshot()
        snap.update({
            "thinking": self.thinking, "running": self.running, "version": self.version,
            "depot": w.day["depot"],
            "orders": [{"id": o["id"], "lat": o["lat"], "lng": o["lng"], "kind": o["kind"], "name": o.get("name"),
                        "street": o.get("street"), "promise": hhmm(o["promise_min"]),
                        "delivered": hhmm(w.delivered[i + 1]) if (i + 1) in w.delivered else None,
                        "late": (w.delivered[i + 1] - w.promised(i + 1)) > 0 if (i + 1) in w.delivered else None,
                        "urgent": (i + 1) in w.priority}
                       for i, o in enumerate(w.orders)],
            "inbox": w.inbox[-12:],
            "to_drivers": w.messages_out[-12:],
            "timeline": [dict(e, t=hhmm(e["t"])) for e in w.events if e["kind"] in ("replan", "handled", "to_driver")][-14:],
            "refunds": list(w.refunds.values()),
            "approvals": [a for a in self.approvals.values() if a["decision"] is None],
            "forecast": self.d.forecast,
        })
        return snap


LIVE = Live()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api/state"):
            return self._json(LIVE.state())
        rel = "index.html" if self.path in ("/", "") else self.path.lstrip("/").split("?")[0]
        f = (WEB / rel).resolve()
        if WEB not in f.parents and f != WEB / "index.html" or not f.exists():
            self.send_response(404); self.end_headers(); return
        ctype = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}.get(f.suffix, "application/octet-stream")
        b = f.read_bytes()
        self.send_response(200); self.send_header("content-type", ctype); self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/start":
            return self._json({"started": LIVE.start(int(body.get("seed", 7)), bool(body.get("scripted", True)))})
        if self.path == "/api/chaos" and LIVE.chaos:
            LIVE.chaos.press(body.get("kind", ""))
            return self._json({"ok": True})
        if self.path == "/api/approve":
            a = LIVE.approvals.get(body.get("id"))
            if a:
                a["decision"] = bool(body.get("approve"))
            return self._json({"ok": bool(a)})
        self._json({"error": "not found"}, 404)


def main():
    neatlogs.init(workflow_name="detour-live", tags=["detour", "live", LIVE.version])
    port = int(os.environ.get("PORT", 8787))
    print(f"Detour live on http://localhost:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()


if __name__ == "__main__":
    main()
