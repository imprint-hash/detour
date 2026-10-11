# Improvement rounds

Each round changes one thing, replays the same three seeds (1, 2, 3), and keeps the change only if the numbers say so.
Model for these runs: `claude-haiku` through LiteLLM (about $0.06 a run). Failures were found by `python -m detour.review`, which reads each run's tool-call log (the same calls neatlogs traces) and checks for wrong ids, missed replans, driver spam and ignored requests. Raw results are in `results/`.

| Round | Change | On time (of 108) | Urgent | Stops reshuffled | Model calls / run | Verdict |
|---|---|---|---|---|---|---|
| 0 | baseline: the v2 dispatcher | 107.7 | 3/3 | 25.3 | 26.7 | start |
| 1 | `MOVE_COST` 60 → 250 in the solver, and urgent stops are exempt from it | 107.7 | 3/3 | **4.7** | 25.3 | **kept** (5x fewer reshuffles, same on-time) |
| 2 | prompt: "the new messages are already in the prompt, don't call read_inbox/get_fleet unless needed" | **108.0** | 3/3 | 5.7 | **20.7** | **kept** (-18% calls, -17% cost) |
| 3 | tools accept "Van 3" / "v3" / "o013" for ids | 107.7 | 3/3 | 3.0 | 19.3 | **kept** (Sonnet used "Van 3" and was refused; no wrong ids since) |

| 4 | from neatlogs: both Fixes applied (prompt: "record every change, then replan once"; "use the short van id"), and a safety net for the rule neatlogs caught, "A parcel was left with no van" (v2 replans by itself if the model finished a batch with a stop on nobody's list) | 107.0 | 5/5 | 10.8 | 21.8 | **kept** (0 parcels lost in 5 days, against 2 for v1; 0 rule breaks for both Fixes) |

Round 4 is measured on seeds 1-5 (the `final` batch) and its rule counts come from neatlogs detections, not from our checker; see the README.

Dead ends, recorded honestly:
- **Round 1 first try** (raise `MOVE_COST` to 250 with no exemption): reshuffles fell to 3, but the urgent medicine order missed 10:45 in all three seeds, because holding the stop with its van beat the late penalty. Fixed by the urgent exemption above (this is why round 1 has two parts).
- **The test itself was unfair at first.** The urgent-order event picked its target after 10:05, by which time some versions had already delivered every pharmacy, so they got a free pass (0/0), and one pick (a clinic across the county) was impossible. The event now fires at 09:50 and picks a stop a van can reach by 10:35. Round 0 was re-run after that fix; the numbers above are all on the fixed test.
- **Nothing to fix in the prompt for decision quality with Haiku.** The checker found zero wrong ids, missed replans, driver spam or ignored requests in the v2 runs. v1 had them (see the table in the README). The one real cost of v2 is below.

## Known trade-off
On seed 7 with Sonnet 4.6, v2 delivered 106/108 on time against v1's 108/108 (final showcase run): keeping stops with their van left O089 68 minutes late after the breakdown and closure, and O022 16 minutes late. With `MOVE_COST` 60 the same day was 107/108 on Groq. `DETOUR_MOVE_COST=120` is the next thing to try; it was not run.
