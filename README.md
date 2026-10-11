# Detour

**An AI dispatcher that keeps a delivery fleet on time when the day falls apart, and proves it.**

Built for [neatHack 2026](https://neatlogs.com/hackathon) with **neatlogs**, **Entire** and **cfo.ai**.

---

## For judges: 60 seconds

| What | Where |
|---|---|
| **neatlogs traces** | TODO: paste trace links. Runs are tagged `final` (seeds 1-5, v1 vs v2), `fault-*` (broken on purpose) and `showcase` (Claude Sonnet 4.6, seed 7) |
| **Entire checkpoint** | TODO: paste the checkpoint link for the v1 → v2 session |
| **cfo.ai** | TODO: paste the cfo.ai link |
| **How we improved it** | [ROUNDS.md](ROUNDS.md): three rounds, each kept or reverted, with numbers |
| **Raw results** | [results/](results/) |

**v1 vs v2, average of seeds 1-5** (same chaos for both; model `claude-haiku`; 10 runs, $0.60 in total):

| Report card | v1 | v2 |
|---|---|---|
| Deliveries on time (of 108) | 107.0 | **107.4** |
| Urgent medicine on time | 5/5 | 5/5 |
| Customer's chosen hour honoured | 5/5 | 5/5 |
| Stops reshuffled between drivers | 129.2 | **9.8** |
| Dispatcher mistakes per day (wrong id, missed replan, driver spam, ignored request) | 1.8 | **0** |
| Refunds paid | $5.99 | $4.39 |

**Showcase day, Claude Sonnet 4.6, seed 7:** v1 108/108 on time but 137 stops reshuffled; v2 105/108 on time (3 late, $19.97 refunded) and 9 reshuffled. Keeping stops with their driver cost three late parcels that day; we say so in [ROUNDS.md](ROUNDS.md#known-trade-off).

**Broken on purpose** (seed 1, v2; `python -m detour.run --fault <name>`):

| What broke | What Detour did | On time |
|---|---|---|
| Solver times out on every replan | Retried with a faster solve and carried on | 105/108 |
| Solver never answers | Kept each van's stops and added the rest by cheapest insertion | 95/108 (worse, but nothing dropped) |
| Model endpoint fails (dropped connections, rate limits) | Waited and retried; every message still handled | 107/108 |
| Every tool fails once | Retried each call once; found by this test: before the retry, one message was left unhandled (5/6), after it 6/6 | 107/108 |
| Road-name service (OSRM) down | The street-name lookup falls back to the nearest named stop (`python -m detour.faults`). A running day reads saved road times, so it does not depend on OSRM | n/a |

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

A map of the fleet, the inbox with what Detour did about each message, what it told the drivers, refunds, and a **Chaos Button**: break a van, close a road, send an urgent medicine order, a traffic jam, a customer who's out, a locked gate.

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
| **neatlogs** | Traces every model call, tool call and decision; catches where the dispatcher went wrong |
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
