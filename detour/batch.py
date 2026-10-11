"""Run several seeds for several versions and average the report cards.

    python -m detour.batch --seeds 1 2 3 4 5 --versions v1 v2 --tag base
Each run is a fresh process (one neatlogs trace each); results land in runs/ and in
runs/batch-<tag>.json.
"""
import argparse, json, subprocess, sys, os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .review import review, counts, CATEGORIES
from .rules import RULES

RUNS = Path(__file__).resolve().parent.parent / "runs"


def num(card, key):
    v = card[key]["value"]
    if key == "on_time":
        a, b = v.split("/"); return int(a)
    if key in ("urgent_medicine_on_time", "customer_requests_honoured", "messages_handled"):
        return int(v.split("/")[0])
    if key == "stops_reshuffled_between_drivers":
        return int(v.split()[0])
    if key == "driver_messages_needed":
        return int(v.split()[2]) if False else int(v.split("needed, ")[1].split()[0])
    if key == "refunds_usd":
        return float(v.strip("$"))


METRICS = {"on_time": "on time (of 108)", "urgent_medicine_on_time": "urgent medicine (of 1)",
           "customer_requests_honoured": "customer window honoured (of 1)",
           "driver_messages_needed": "driver-message noise", "stops_reshuffled_between_drivers": "stops reshuffled",
           "messages_handled": "messages handled (of 6)", "refunds_usd": "refunds $"}


def run_one(seed, version, tag, extra):
    cmd = [sys.executable, "-m", "detour.run", "--seed", str(seed), "--version", version, "--tag", tag] + extra
    r = subprocess.run(cmd, capture_output=True, text=True)
    new = [l.split(" ", 1)[1].strip() for l in r.stdout.splitlines() if l.startswith("RUNFILE ")]
    if not new:
        return {"seed": seed, "version": version, "error": (r.stderr or r.stdout)[-400:]}
    run = json.load(open(new[0]))
    return {"seed": seed, "version": version, "file": os.path.basename(new[0]), "card": run["report"],
            "mistakes": counts(review(run)), "trace_id": run.get("trace_id"),
            "rules_broken": {r: sum(1 for x in run.get("decision_log", []) for b in x["broken"] if b["rule"] == r) for r in RULES.values()}, "late": len(run["late"]), "recoveries": len(run["recoveries"]),
            "usd": run["usage"].get("usd", 0), "calls": run["usage"]["calls"], "seconds": run["seconds"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    ap.add_argument("--versions", nargs="+", default=["v1", "v2"])
    ap.add_argument("--tag", required=True)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--provider", default="litellm")
    ap.add_argument("--model", default="claude-haiku")
    ap.add_argument("--fault", action="append", default=[])
    a = ap.parse_args()
    out = []
    jobs = [(seed, v) for seed in a.seeds for v in a.versions]
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for res in pool.map(lambda j: run_one(j[0], j[1], a.tag, ["--provider", a.provider, "--model", a.model] + [x for f in a.fault for x in ("--fault", f)]), jobs):
            out.append(res)
            print(res["seed"], res["version"], res.get("mistakes", res.get("error")), f"${res.get('usd', 0):.3f}", flush=True)
            (RUNS / f"batch-{a.tag}.json").write_text(json.dumps(out, indent=1))
    print_table(out)


def print_table(out):
    good = [r for r in out if "card" in r]
    versions = sorted({r["version"] for r in good})
    print(f"\n{'metric':36s}" + "".join(f"{v:>10s}" for v in versions))
    for k, label in METRICS.items():
        row = []
        for v in versions:
            xs = [num(r["card"], k) for r in good if r["version"] == v]
            row.append(f"{sum(xs) / len(xs):10.1f}" if xs else f"{'-':>10s}")
        print(f"{label:36s}" + "".join(row))
    for c in CATEGORIES:
        row = []
        for v in versions:
            xs = [r["mistakes"][c] for r in good if r["version"] == v]
            row.append(f"{sum(xs) / len(xs):10.1f}" if xs else f"{'-':>10s}")
        print(f"{'mistake: ' + c:36s}" + "".join(row))
    for r in RULES.values():
        row = []
        for v in versions:
            xs = [x.get("rules_broken", {}).get(r, 0) for x in good if x["version"] == v]
            row.append(f"{sum(xs) / len(xs):10.1f}" if xs else f"{'-':>10s}")
        print(f"{'rule: ' + r[:30]:36s}" + "".join(row))
    print("spend: $%.2f over %d model calls" % (sum(r["usd"] for r in good), sum(r["calls"] for r in good)))
    print("runs:", {v: sum(1 for r in good if r["version"] == v) for v in versions}, "failed:", len(out) - len(good))


if __name__ == "__main__":
    main()
