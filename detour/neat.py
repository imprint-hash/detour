"""Detour's side of neatlogs beyond tracing: a detection for every business rule, and
the manager's rules (from the Correct button).

Uses the neatlogs Public API with the service-account token NEATLOGS_TOKEN. Without it,
falls back to the neatlogs MCP server (token from NEATLOGS_MCP_TOKEN or the git-ignored
.mcp.json), which is also where a new rule is previewed against real traces.

    python -m detour.neat sync      # make sure every rule in detour/rules.py has its detection
    python -m detour.neat list      # the rules neatlogs is watching
    python -m detour.neat report runs/batch-final.json   # neatlogs' own count of broken rules, v1 vs v2
"""
import json, os, re, sys, uuid
from pathlib import Path
import httpx

from .rules import RULES

ROOT = Path(__file__).resolve().parent.parent
GROUP = "Dispatcher rules"
SPANS = ["dispatcher_decision", "manager_feedback"]
MANAGER = "Manager: "   # name prefix of a rule added from the Correct button
CACHE = ROOT / "runs" / "manager-rules.json"
API = os.environ.get("NEATLOGS_HOST", "https://app.neatlogs.com") + "/api/v1/public"
MCP_URL = os.environ.get("NEATLOGS_MCP_URL", "https://ingest.neatlogs.com/mcp")
TRACE_URL = os.environ.get("NEATLOGS_TRACE_URL", "https://app.neatlogs.com/project/{project}/traces/{trace}")


def trace_url(trace_id, span_id=None):
    if not trace_id:
        return None
    return TRACE_URL.format(project=os.environ.get("NEATLOGS_PROJECT_ID", ""), trace=trace_id, span=span_id or "")


def pattern(rule):
    return "RULE BROKEN: " + re.escape(rule)


class MCP:
    def __init__(self):
        tok = os.environ.get("NEATLOGS_MCP_TOKEN") or \
            json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["neatlogs"]["headers"]["Authorization"].split(" ", 1)[1]
        self.h = {"Authorization": f"Bearer {tok}", "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        self.c = httpx.Client(timeout=60)
        self.n = 0
        r = self._post({"method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                                           "clientInfo": {"name": "detour", "version": "1"}}})
        if r.headers.get("mcp-session-id"):
            self.h["mcp-session-id"] = r.headers["mcp-session-id"]
        self.c.post(MCP_URL, headers=self.h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _post(self, body):
        self.n += 1
        r = self.c.post(MCP_URL, headers=self.h, json={"jsonrpc": "2.0", "id": self.n, **body})
        r.raise_for_status()
        return r

    def call(self, tool, **args):
        r = self._post({"method": "tools/call", "params": {"name": tool, "arguments": args}})
        text = r.text
        if "text/event-stream" in r.headers.get("content-type", ""):
            text = "\n".join(l[5:] for l in text.splitlines() if l.startswith("data:"))
        res = json.loads(text)["result"]
        out = res["content"][0]["text"]
        if res.get("isError"):
            raise RuntimeError(out[:300])
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return out


class Neat:
    """Detections in the 'Dispatcher rules' group, through the Public API or MCP."""

    def __init__(self):
        self.tok = os.environ.get("NEATLOGS_TOKEN")
        self.c = httpx.Client(timeout=60)
        self._mcp = None
        self.gid = self._group()

    @property
    def mcp(self):
        self._mcp = self._mcp or MCP()
        return self._mcp

    def _api(self, method, path, body=None):
        h = {"Authorization": f"Bearer {self.tok}", "x-project-id": os.environ.get("NEATLOGS_PROJECT_ID", "")}
        if method == "POST":
            h["idempotency-key"] = str(uuid.uuid4())
        r = self.c.request(method, API + path, headers=h, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"neatlogs {method} {path}: {r.status_code} {r.text[:300]}")
        return r.json()["data"]

    def _group(self):
        if self.tok:
            for g in self._api("GET", "/detection-groups?limit=100")["groups"]:
                if g["name"] == GROUP:
                    return g["id"]
            return self._api("POST", "/detection-groups", {"name": GROUP})["id"]
        for g in self.mcp.call("list_detection_groups")["groups"]:
            if g["name"] == GROUP:
                return g["id"]
        return self.mcp.call("create_detection_group", name=GROUP)["id"]

    def detections(self):
        """[{id, name, active}] for every rule neatlogs is watching."""
        if self.tok:
            out, cursor = [], ""
            while True:
                page = self._api("GET", f"/detections?groupId={self.gid}&limit=100" + (f"&cursor={cursor}" if cursor else ""))
                out += [{"id": d["id"], "name": d["displayName"], "active": d["isActive"]} for d in page["detections"]]
                cursor = page.get("page", {}).get("nextCursor")
                if not page.get("page", {}).get("hasMore"):
                    return out
        return [{"id": d["id"], "name": d["display_name"], "active": d["is_active"]}
                for d in self.mcp.call("list_detections", group_id=self.gid)["detections"]]

    def preview(self, rule, days=14):
        """How many recent decision records neatlogs would flag (MCP only; None if unavailable)."""
        try:
            p = self.mcp.call("preview_detection", type="regex", config={"output_pattern": pattern(rule), "case_sensitive": True},
                              run_on={"frequency": "span_name", "span_names": SPANS}, timeframe_days=days, limit=5)
            return p.get("total_count") if isinstance(p, dict) else None
        except Exception:
            return None

    def ensure(self, rule, description):
        """One neatlogs detection per rule: it flags any decision record that says the rule was broken."""
        for d in self.detections():
            if d["name"] == rule:
                return {"id": d["id"], "created": False}
        config = {"output_pattern": pattern(rule), "case_sensitive": True, "operator": "OR"}
        run_on = {"frequency": "span_name", "span_names": SPANS}
        if self.tok:
            made = self._api("POST", "/detections", {"type": "regex", "displayName": rule[:255], "groupId": self.gid,
                                                     "description": description, "isActive": True, "threshold": 0.5,
                                                     "runOn": run_on, "config": config})
        else:
            made = self.mcp.call("create_detection", display_name=rule[:255], description=description, group_id=self.gid,
                                 type="regex", config=config, run_on=run_on, threshold=0.5)
        return {"id": made.get("id"), "created": True}


    def hits(self, detection_id):
        """Every (trace, span) neatlogs flagged for one detection (Public API)."""
        out, cursor = [], ""
        while True:
            page = self._api("GET", f"/detections/{detection_id}/hits?limit=100" + (f"&cursor={cursor}" if cursor else ""))
            out += [(h["traceId"], h["spanId"]) for h in page["hits"]]
            cursor = page["page"].get("nextCursor")
            if not page["page"]["hasMore"]:
                return out


def report(batch_file):
    """Read back from neatlogs how often each rule fired on each version of a batch."""
    runs = {r["trace_id"]: r for r in json.loads(Path(batch_file).read_text()) if r.get("trace_id")}
    versions = sorted({r["version"] for r in runs.values()})
    n, table = Neat(), []
    for d in n.detections():
        flagged = {}
        for tid, sid in set(n.hits(d["id"])):
            if tid in runs:
                flagged.setdefault(runs[tid]["version"], []).append(tid)
        row = {"rule": d["name"], **{v: len(flagged.get(v, [])) for v in versions},
               **{f"{v}_runs": len(set(flagged.get(v, []))) for v in versions}}
        table.append(row)
        print(f"{d['name'][:64]:66s}" + "".join(f"{v}: {row[v]:3d} hits in {row[v + '_runs']}/{sum(1 for r in runs.values() if r['version'] == v)} runs   " for v in versions))
    return table


def manager_rules():
    """The manager's rules, read from neatlogs (the source of truth), with a local copy if it is unreachable."""
    try:
        out = [d["name"] for d in Neat().detections() if d["name"].startswith(MANAGER) and d["active"]]
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(out, indent=1))
        return out
    except Exception:
        return json.loads(CACHE.read_text()) if CACHE.exists() else []


def sync():
    n = Neat()
    for key, rule in RULES.items():
        r = n.ensure(rule, f"Business rule '{key}', checked by detour/rules.py on every dispatcher decision. "
                           f"Flags a dispatcher_decision span whose record says this rule was broken.")
        print(("created " if r["created"] else "exists  ") + rule, r["id"], "| preview hits (14 days):", n.preview(rule))


if __name__ == "__main__":
    if (sys.argv[1:] or ["list"])[0] == "sync":
        sync()
    elif sys.argv[1] == "report":
        out = report(sys.argv[2])
        if len(sys.argv) > 3:
            Path(sys.argv[3]).write_text(json.dumps(out, indent=1))
    else:
        for d in Neat().detections():
            print(f"{'on ' if d['active'] else 'off'} {d['name']}  {d['id']}")
