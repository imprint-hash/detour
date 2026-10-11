# Detour: notes for the coding agent

Detour is an AI dispatcher for a delivery fleet, built for neatHack 2026 (judges from neatlogs and Entire).
Submission window: Mon 12 Oct 2026, 7:00–11:55 pm IST (14:30–19:25 WAT). Required tools: neatlogs, Entire, cfo.ai.

## How it fits together
- `detour/prep.py` builds `data/day.json`: real Amazon stops (DLA7 depot, Chino CA, 22 Jul 2018), real OSM businesses, OSRM road times.
- `detour/world.py` is the simulator (clock, vans, deliveries, closures, scoring). `World.promised(n)` is the one rule for when an order is due.
- `detour/solver.py` is the OR-Tools route maths.
- `detour/chaos.py` holds the scripted, seeded events plus the Chaos Button.
- `detour/agent.py` is the dispatcher: an OpenAI-SDK tool loop, traced in neatlogs with `@span`.
- `detour/rules.py` holds the business rules; after each batch of messages the dispatcher writes a `dispatcher_decision` span (plain-English record + `RULE BROKEN: <rule>` lines). Manager rules from the live screen's 👎 are judged by the model.
- `detour/neat.py` manages the neatlogs detections (one regex detection per rule, group "Dispatcher rules"; `python -m detour.neat sync|list`). Uses `NEATLOGS_TOKEN` (service account) or falls back to the MCP token. Manager rules are detections named `Manager: …`; neatlogs is their store.
- `detour/evals.py` is the report card. `detour/run.py` plays a headless day; `detour/server.py` + `web/index.html` give the live map (`./serve.sh`, port 8787).

## Commands
```bash
set -a; . ./.env; set +a
.venv/bin/python -m detour.run --seed 7 --version v1     # headless day + report card
./serve.sh                                               # live screen
```
Install Python packages with `/home/imprint/opt/kokoro-venv/bin/python -m pip --python .venv/bin/python install <pkg>` (this box has no ensurepip). Ask the user before installing anything new.

## Rules
- Keys live only in `.env` (git-ignored). Never paste a key into chat or code: this repo is public and Entire checkpoints are public.
- No AI attribution in commits (no "Co-Authored-By: Claude", no "Generated with").
- Talk to the user in simple, friendly English. They name things; ask before adding features.
- Don't restart the server with `pkill -f detour`; it kills your own shell. Use `./serve.sh`.

## What to do next: the v1 → v2 fix (this is what Entire records)
v1 baseline on seed 7: 106/108 on time, and **114 stops reshuffled between drivers over 6 replans**. The findings are in `NOTES-v1.md`. Fix, then rerun seed 7 with `--version v2` and compare report cards:
1. Route stability: the solver should prefer keeping each stop with its current van (cost for moving a stop), except stops stranded on a broken van.
2. Customer window: `set_customer_window` should move the promise to `not_before + 60`.
3. Driver messages: only vans whose stop list changed, saying what changed.
4. Data: a nearest-named-street fallback for stops whose road has no name.
