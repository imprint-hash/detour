# Detour

**An AI dispatcher that keeps a delivery fleet on time when the day falls apart, and proves it.**

Built for [neatHack 2026](https://neatlogs.com/hackathon) with **neatlogs**, **Entire** and **cfo.ai**.

---

## For judges: 60 seconds

| What | Where |
|---|---|
| **neatlogs: the rules** | 7 business rules, plus every rule the manager adds with 👎, live as neatlogs detections in the group **Dispatcher rules** (e.g. "Customer's requested time was not honoured", "A replan reshuffled many stops between drivers"). Every dispatcher decision is a `dispatcher_decision` span; neatlogs flags the ones that break a rule. [How it works](#neatlogs-is-the-dispatchers-manager) |
| **neatlogs: v1 → v2 → v3, counted by neatlogs** | The detections fire on v1; v2 fixed most of them; v3 (round 5) fixed the two neatlogs still flagged on v2. 0 alarms on v3 (table below, read back from neatlogs' own detection hits: [results/neatlogs-final.json](results/neatlogs-final.json), [results/neatlogs-round5.json](results/neatlogs-round5.json)) |
| **neatlogs: the 👎 button** | On the live screen, 👎 a decision and write what it should have done. The note becomes a new neatlogs detection, and the model checks every future decision against it, live and headless |
| **neatlogs: Fixes** | neatlogs raised 2 Fixes from our traces (van name used as id; replanning after every action). Both applied, verified on the `final` runs, and marked resolved in neatlogs with the evidence |
| **neatlogs traces** | TODO: paste share links. Tags: `final` (seeds 1-5, v1 and v2, trace ids in [results/batch-final.json](results/batch-final.json)), `round5` (seeds 1-5, v3, trace ids in [results/batch-round5.json](results/batch-round5.json)), `showcase` (Claude Sonnet 4.6, seed 7: v1 `e6de1d6bc4208df5ee3c797bde4a83bf`, v2 `1ac7216384de59cb7209ca8f699aaee4`), `fault-*` (broken on purpose) |
| **Entire checkpoint** | TODO: paste the checkpoint link for the v1 → v2 session |
| **cfo.ai** | The business plan: [cfo.ai/s/BG0Igr7QyRwQsLW5](https://cfo.ai/s/BG0Igr7QyRwQsLW5) · spreadsheet [docs/cfo/detour-cfo-plan.xlsx](docs/cfo/detour-cfo-plan.xlsx) |
| **How we improved it** | [ROUNDS.md](ROUNDS.md): five rounds, each kept or reverted, with numbers |
| **Raw results** | [results/](results/) |

**v1 vs v2 vs v3, seeds 1-5** (same chaos for all; model `claude-haiku`; 15 runs, $0.91 in total for the dispatcher, plus one small model call per decision to check the manager's rule):

| Report card (average per day) | v1 | v2 | v3 |
|---|---|---|---|
| Deliveries on time (of 108) | **107.4** | 107.0 | **107.4** |
| Parcels never delivered (all 5 days) | 2 | **0** | **0** |
| Urgent medicine on time | 5/5 | 5/5 | 5/5 |
| Customer's chosen hour honoured | 5/5 | 5/5 | 5/5 |
| Stops reshuffled between drivers | 126.8 | 10.8 | **9.4** |
| Refunds paid | $1.20 | $5.99 | $3.59 |

**The same days, judged by neatlogs** (detection hits on the `final` and `round5` traces, from the neatlogs API):

| neatlogs detection | v1: runs flagged (hits) | v2: runs flagged (hits) | v3: runs flagged |
|---|---|---|---|
| A replan reshuffled many stops between drivers | 5/5 (20) | **0/5** | **0/5** |
| Customer's requested time was not honoured | 5/5 (5) | **0/5** | **0/5** |
| A parcel was left with no van to deliver it | 3/5 (3) | **0/5** | **0/5** |
| Dispatcher messaged a driver whose route did not change | 0/5 | 0/5 | 0/5 |
| Dispatcher used an id that does not exist (neatlogs Fix 1) | 0/5 | 0/5 | 0/5 |
| Dispatcher replanned more than once for one batch (neatlogs Fix 2) | 0/5 | 0/5 | 0/5 |
| Urgent medicine order was not prioritised | 0/5 | 2/5 (2) | **0/5** |
| Manager: When a van breaks down, tell that driver who is collecting their parcels and when (from 👎) | 5/5 (5) | 5/5 (5) | **0/5** |

On v2, neatlogs caught two things. On two days the 09:50 plan left the insulin 47-54 minutes late and the dispatcher accepted it; it was only on time because the 10:15 breakdown forced a better replan. And the manager's 👎 rule (tell a stranded driver who is collecting their parcels, and when) failed every day on both versions. **v3 (round 5) fixes both:** after each replan, an urgent order forecast late is moved to wherever on the fleet it arrives on time at the least cost to everyone else, and `replan` hands the dispatcher the breakdown handoff ("V2 collects O020, O062…; V5 collects O017…, from about 10:40") to pass on. All 30 v3 decisions keep every rule ([ROUNDS.md](ROUNDS.md)).

v3 matches v1 on time and keeps v2's stability: about 13x fewer reshuffled stops than v1, and every parcel delivered (v1 left one with no van on 2 of 5 days).

**Showcase day, Claude Sonnet 4.6, seed 7:** v1 108/108 on time, 146 stops reshuffled, 6 rule breaks flagged (5 reshuffles, 1 ignored customer time); v2 106/108 (2 late, $13.98 refunded), 20 reshuffled, the only flag the manager's breakdown rule. $0.51 for both.

**Broken on purpose** (seed 1, v2; `python -m detour.run --fault <name>`):

| What broke | What Detour did | On time |
|---|---|---|
| Solver times out on every replan | Retried with a faster solve and carried on | 105/108 |
| Solver never answers | Kept each van's stops and added the rest by cheapest insertion | 95/108 (worse, but nothing dropped) |
| Model endpoint fails (dropped connections, rate limits) | Waited and retried; every message still handled | 107/108 |
| Every tool fails once | Retried each call once; found by this test: before the retry, one message was left unhandled (5/6), after it 6/6 | 107/108 |
| Road-name service (OSRM) down | The street-name lookup falls back to the nearest named stop (`python -m detour.faults`). A running day reads saved road times, so it does not depend on OSRM | n/a |

## neatlogs is the dispatcher's manager

A dispatch manager doesn't read every log line. They hold the dispatcher to a few rules. Detour gives neatlogs those rules:

1. **Every decision is a span.** After each batch of messages, the dispatcher writes a `dispatcher_decision` span in plain English: what came in, every tool it called, which drivers' stops changed, who it messaged, the forecast, and one `RULE BROKEN: <rule>` line for each rule it broke ([detour/rules.py](detour/rules.py)).
2. **Every rule is a detection.** Each rule is a neatlogs detection named in plain English (`python -m detour.neat sync`), so neatlogs flags, counts and trends rule breaks on every trace, live or headless.
3. **The manager adds rules with 👎.** On the live screen, each decision has a neatlogs trace link and a 👎. The manager writes what should have happened; Detour adds a `manager_feedback` span inside that decision's trace and creates a neatlogs detection `Manager: <note>`. From then on, the model checks every decision against the manager's rules, which are read from neatlogs at the start of each run.
4. **neatlogs Fixes drive the fixes.** neatlogs read our traces and proposed two Fixes. We applied them, checked them on the `final` runs, and marked them resolved. Each Fix also became a detection, so it can't quietly come back.
5. **neatlogs keeps the score.** `python -m detour.neat report results/batch-final.json` reads the detection hits back from neatlogs and splits them by version (the table above).

neatlogs' detections are regex, conditional, PII or one of four preset classifiers; there is no free-text classifier in its API. So the plain-English judging happens in Detour (the code, plus the model for the manager's rules), and neatlogs does the watching, counting and alerting.

## The problem

A delivery day never goes to plan. A van breaks down at 10:15 with twelve parcels on board. Police close the roads around a whole neighbourhood. The warehouse calls: insulin for a pharmacy, needed by 10:45. A customer texts that she won't be home until two. A driver is standing at a locked gate.

Each of those arrives as a message, written in a hurry, and somebody in an office has to read it, work out what it really changes, re-plan every van, tell the right drivers, and make sure the parcels actually land. Route-planning software does the maths, but it can't read a driver's text. Chatbots can read the text, but they can't run a fleet.

So the person in the office does it by hand, under pressure, and parcels arrive late. Late parcels cost money twice: the refund, and the customer who doesn't come back.

## The solution

Detour is the dispatcher. It runs the fleet for the whole day:

1. **Reads every message**: drivers, customers, the warehouse, city traffic alerts.
2. **Works out what changed**: a van out of action, a closed road, an urgent deadline, a customer who asked for later.
3. **Re-plans the whole fleet** with real route maths (Google OR-Tools on real road travel times), putting medicine ahead of houses when not everything can be on time.
4. **Tells the drivers** whose route changed, in one plain line.
5. **Checks every delivery** against what was promised.
6. **Refunds late deliveries once**, automatically for small amounts. Anything over $10 waits for a person to approve it.

Every decision is traced in **neatlogs**. When Detour gets something wrong, the trace shows exactly where, the fix is recorded in **Entire**, and the same chaotic day is replayed to prove the fix worked.

## What's real

| Part | Source |
|---|---|
| Delivery stops and depot | Two real Amazon routes from the DLA7 depot (Chino, California), 22 July 2018, from the [Amazon Last Mile Routing Research Challenge](https://registry.opendata.aws/amazon-last-mile-challenges) (CC BY-NC 4.0) |
| Business customers | Real pharmacies, clinics and restaurants from [OpenStreetMap](https://www.openstreetmap.org) |
| Road travel times and street names | [OSRM](https://project-osrm.org) on OpenStreetMap roads |
| The chaos | Scripted and seeded, so the same day can be replayed exactly. The Chaos Button adds more on demand |
| Delivery promises and fees | Set by us (the Amazon sample carries almost none) |

## The live screen

A map of the fleet, the inbox with what Detour did about each message, every **decision** with its neatlogs trace link, the rules it broke and a 👎 button, what it told the drivers, refunds, and a **Chaos Button**: break a van, close a road, send an urgent medicine order, a traffic jam, a customer who's out, a locked gate.

```bash
./serve.sh            # then open http://localhost:8787
```

## The report card

Every run is scored on the checks a dispatch manager would hold a person to:

- deliveries on time
- urgent medicine on time
- customers who asked for a later time got that hour
- stops reshuffled between drivers (drivers notice every one)
- every message handled
- refunds paid

```bash
python -m detour.run --seed 7 --version v1
```

## Built with

| Tool | What it does in Detour |
|---|---|
| **neatlogs** | Traces every model call, tool call and decision; holds the business rules as detections; turns the manager's 👎 into new rules; its Fixes drove two of the improvements |
| **Entire** | Checkpoints of every change to the dispatcher, so each fix can be seen and rewound |
| **cfo.ai** | The business plan: pricing per van, costs, revenue, runway |
| Google OR-Tools | The route maths |
| OpenAI SDK | Talks to the model through LiteLLM |

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install neatlogs ortools httpx openai
cp .env.example .env      # neatlogs key, model endpoint
.venv/bin/python -m detour.prep    # rebuild the day from the public data (optional)
./serve.sh
```

## Licence

Code: MIT. Amazon data: CC BY-NC 4.0, used here for a non-commercial demo. OpenStreetMap data: ODbL.
