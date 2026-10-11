"""Play one chaotic day with Detour in charge, headless, and score it.

    python -m detour.run --seed 7 --version v1
"""
import argparse, json, time, uuid
from pathlib import Path
import neatlogs
from neatlogs.decorators import span

from .world import World, hhmm
from .chaos import Chaos
from .agent import Dispatcher
from .faults import Faults
from .evals import report, print_report

RUNS = Path(__file__).resolve().parent.parent / "runs"


@span(kind="WORKFLOW", name="detour_day")
def play(seed, version, end_min=16 * 60, on_step=None, provider="groq", model=None, faults=()):
    w = World()
    chaos = Chaos(seed)
    d = Dispatcher(w, version=version, provider=provider, model=model, faults=Faults(faults))
    d.replan()  # the morning plan, before anything goes wrong
    while w.clock < end_min and len(w.delivered) < len(w.orders):
        new = chaos.due(w)
        if new:
            d.handle(new)
        w.step(1)
        if on_step:
            on_step(w, d)
    d.close_out()
    return w, d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--version", default="v1")
    ap.add_argument("--provider", default="groq", choices=["groq", "litellm"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--fault", action="append", default=[], help="break something on purpose (see detour/faults.py)")
    ap.add_argument("--tag", default="", help="label for this run, e.g. a round name")
    a = ap.parse_args()
    run_id = f"{a.version}-s{a.seed}-{uuid.uuid4().hex[:6]}"
    neatlogs.init(workflow_name="detour", tags=["detour", a.version, f"seed-{a.seed}", a.provider] + a.fault + ([a.tag] if a.tag else []))
    t0 = time.time()
    w, d = play(a.seed, a.version, provider=a.provider, model=a.model, faults=a.fault)
    neatlogs.flush()
    s = w.score()
    card = report(w)
    unhandled = [m for m in w.inbox if not m["handled"]]
    out = {"run": run_id, "version": a.version, "seed": a.seed, "provider": a.provider, "model": d.model, "faults": a.fault, "tag": a.tag,
           "decisions": d.decisions, "recoveries": d.recoveries, "injected": d.faults.injected, "score": s, "seconds": round(time.time() - t0),
           "unhandled_messages": [m["text"][:80] for m in unhandled],
           "inbox": w.inbox, "to_drivers": w.messages_out, "refunds": list(w.refunds.values()),
           "late": [e for e in w.events if e["kind"] == "delivered" and e["late_min"] > 0],
           "report": card, "events": w.events}
    RUNS.mkdir(exist_ok=True)
    (RUNS / f"{run_id}.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: out[k] for k in ("run", "score", "seconds", "unhandled_messages")}, indent=1))
    print("late:", [(e["order"], e["late_min"]) for e in out["late"]][:15])
    print_report(card, f"Report card · {a.version} · seed {a.seed}")


if __name__ == "__main__":
    main()
