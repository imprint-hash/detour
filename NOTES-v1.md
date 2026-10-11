# What v1 got wrong (seed 7)

Report card, v1:
- on time 106/108
- stops reshuffled between drivers: **114 over 6 replans** (every replan hands most stops to a different driver)
- 17 driver messages, most of them "route updated, check your plan"
- customer O063 asked for "after 2pm"; the window was recorded but the plan still treated 13:00 as the deadline, so it arrived on the last minute of the hour
- O038 (home) 67 min late after the breakdown + closure
- one CVS Pharmacy order has no street name, so the urgent message reads "CVS Pharmacy ()"

Fixes to make in the Entire-recorded session (start Claude Code inside /home/imprint/detour):
1. Route stability: the solver should prefer keeping each stop with the van that already has it (cost for moving a stop to another van), unless that makes something late. Broken van's stops are the exception.
2. Customer window: set_customer_window should move the promise to not_before+60, so the plan aims for the hour asked.
3. Driver messages: message only vans whose stop list actually changed, and say what changed ("+3 stops: O012, O019, O044") instead of "check your plan".
4. Data: fall back to the nearest named street for stops whose own road has no name.

Then replay seed 7 with --version v2 and compare report cards.
