# Detour

**An AI dispatcher that keeps a delivery fleet on time when the day falls apart, and proves it.**

Built for [neatHack 2026](https://neatlogs.com/hackathon) with **neatlogs**, **Entire** and **cfo.ai**.

---

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
