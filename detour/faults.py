"""Break things on purpose, to show the dispatcher recovers.

    python -m detour.run --seed 7 --version v2 --fault solver_timeout

solver_timeout  the first solver attempt of every replan times out
solver_down     the solver never answers (every attempt fails)
llm_flaky       the model endpoint fails on every 4th call (rate limit, bad output)
tool_flaky      the first call to each tool raises, once
"""
import httpx, openai

FAULTS = {"solver_timeout", "solver_down", "llm_flaky", "tool_flaky"}


class Faults:
    def __init__(self, names=()):
        names = set(names or ())
        bad = names - FAULTS
        if bad:
            raise ValueError(f"unknown fault {sorted(bad)}; pick from {sorted(FAULTS)}")
        self.names, self.llm_calls, self.tools_hit = names, 0, set()
        self.injected = []

    def __bool__(self):
        return bool(self.names)

    def solver(self, attempt):
        if "solver_down" in self.names or ("solver_timeout" in self.names and attempt == 0):
            self.injected.append(f"solver attempt {attempt}")
            raise TimeoutError("solver exceeded its time limit (injected)")

    def llm(self):
        self.llm_calls += 1
        if "llm_flaky" in self.names and self.llm_calls % 4 == 0:
            self.injected.append(f"llm call {self.llm_calls}")
            req = httpx.Request("POST", "http://injected")
            raise openai.APIConnectionError(request=req)

    def tool(self, name):
        if "tool_flaky" in self.names and name not in self.tools_hit:
            self.tools_hit.add(name)
            self.injected.append(f"tool {name}")
            raise ConnectionError(f"{name} backend unavailable (injected)")


def demo_osrm_down():
    """The road-name service is unreachable. Street names must still come out filled.

    (At run time Detour reads the road times saved in data/day.json, so a live OSRM outage
    can only hit the data build; this shows that build recovering.)"""
    import json
    from . import prep
    day = json.loads((prep.DATA / "day.json").read_text())
    want = {o["id"]: o["street"] for o in day["orders"]}
    orders = [dict(o) for o in day["orders"]]
    gone = [o for o in orders if o["id"] in {"O004", "O070", "O100"}]
    for o in gone:
        o["street"] = ""
    real_get = httpx.Client.get
    httpx.Client.get = lambda self, *a, **k: (_ for _ in ()).throw(httpx.ConnectError("OSRM unreachable (injected)"))
    try:
        prep.street_names(gone, only_missing=True)
        still_empty = [o["id"] for o in gone if not o["street"]]
        prep.borrow_streets(orders)
    finally:
        httpx.Client.get = real_get
    print(f"OSRM down: lookups failed for {still_empty}; after the fallback:")
    for o in gone:
        print(f"  {o['id']}: {o['street']!r}  (data/day.json has {want[o['id']]!r})")
    assert all(o["street"] for o in gone)


if __name__ == "__main__":
    demo_osrm_down()
