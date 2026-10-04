# Spike S-3: van routing with OR-Tools and OSRM

**Question:** can OR-Tools and a self-hosted OSRM route 50 stops in the Mumbai region within 30 s?
**Answer: yes (passed).**
- For 50 societies, the first valid plan comes after 0.03 s, and the best plan is found within 5 s (nothing improves from 5 to 30 s).
- Every society is on time, and the plan costs **13 % less per order than nearest neighbour** and **91 % less than one trip per order**.
- The bigger finding is about **vans, not the solver**. Letting each van do two trips cuts the fleet by **40–45 %**. The scalable way to do that is to solve in **two waves** (07–09 societies, then 08–10).

**Owner:** Prathamesh Relekar · **Date:** 4 Oct 2026 · **Code:** `spikes/s3-routing/route.py` (output in `results.txt`, map in `routes.geojson`)
**Run:** `just osrm-prepare && just up-routing`, then `uv run --with ortools==9.15.6755 python spikes/s3-routing/route.py --scale` from the repository root.

## Setup
- **Machine:** Apple M2, 8 cores, 8 GB. OR-Tools 9.15 routing (single-threaded), Python 3.13.
- **Road network:** OSRM v26.10.0 (MLD, car profile) on OpenStreetMap clipped to the Mumbai Metropolitan Region (`infra/osrm/`).
  - The one-time build takes about 3 minutes.
  - The server uses about 100 MB of RAM.
  - Snapping 59 points and a 59 × 59 distance and time table take 0.2 s together.
- **Instance** (seeded, synthetic): a hub in Kharghar and 50 societies:
  - the 5 seed societies plus 45 random points in Kharghar, Kamothe, Kalamboli, Khandeshwar, New Panvel and Belapur, each snapped to a road;
  - 614 orders (4–20 per society, 3–6 kg each), 2,750 kg and 222 crates out (3 orders a crate);
  - 143 empty crates collected on the way (70 % of societies were served yesterday);
  - 8 donation pickups that must reach a food bank in CBD Belapur on the same van (pickup-and-delivery pairs).
- **A stop is a society, not a flat.** The van hands crates to the Society Captain, who does the last 50 m (M7). Each society has the captain's window: 07–09 or 08–10. Service is 4 min plus 30 s per crate handled.
- **Vans** (assumed: an electric cargo three-wheeler; vehicle type is an open question in M7):
  - capacity: 400 kg and 24 crates; leaves from 06:30, back by 11:30;
  - travel time: OSRM car time × 1.5 (morning traffic, slower vehicle; OSRM has no traffic data);
  - cost: ₹250 a van per slot (rent share), ₹108 an hour for the rider, ₹0.60 a km.
  - Lateness costs ₹30 a minute in the model, so it is a last resort, never a hard failure.
- **Load model:** the van leaves with all its drops. Empties and donations take room as drops free it, and the food bank stop unloads the donations. The start load is tied to the route's total drops, so the capacity check is exact at every stop.
- **Baselines** (handbook 08 M-F):
  - **One trip per order:** a bike rider per order, hub → society → hub, 4 min at the door, ₹2.50 a km. The fleet is the smallest that is 95 % on time when dispatched earliest-deadline first. Riders are paid only while busy, which flatters this baseline.
  - **Nearest neighbour:** the same vans, each going to the nearest stop that still fits, leaving just in time for its first window. It ignores the windows.
- **Checks:** every method is scored by one independent `evaluate()`. It replays the routes on the OSRM matrix and checks:
  - time windows, with waiting when early;
  - kg and crate load at every stop;
  - each donation picked up before its food-bank drop, on the same van;
  - every society visited exactly once;
  - back by 11:30;
  - for two-trip vans, that the second trip leaves after the first is back and reloaded (20 min).

## Results: 50 societies, 614 orders
| Method | Vans / riders | ₹ an order | km an order | On time | Solve time |
|---|---|---|---|---|---|
| One trip per order (bikes) | 72 riders | 76.57 | 13.07 | 96 % | 0.05 s |
| Nearest neighbour | 10 | 7.93 | 0.48 | 92 % | < 0.01 s |
| OR-Tools CVRPTW, one trip a van | 10 | **6.87** | **0.35** | **100 %** | 5 s (same at 30 s) |
| OR-Tools, two trips a van (chained in one model) | **6** | **5.39** | 0.39 | 100 % | 30 s + 2 probes of 5 s |
| OR-Tools in two waves | **6** | 5.49 | 0.42 | 100 % | 30 s |

- **Solver speed:**
  - first solution at 0.03 s (₹5,723);
  - ₹4,266 by 1 s and ₹4,219 by 5 s;
  - no improvement from 5 s to 30 s.
- **Against nearest neighbour:** −13 % ₹/order, −27 % km and 100 % against 92 % on time.
- **Against one trip per order:** about 37 times fewer km, which is the society-drop USP in numbers. Even a perfect bike dispatcher would need 72 riders for this one morning.
- **Shift pay:** if riders are paid for the whole 06:30–11:00 shift instead of route time, one trip a van costs ₹13.08 an order. Two trips a van (either way) costs ₹7.96. With shift pay, the fleet size dominates the cost.

## Scale: 100 and 200 societies (same area, so denser)
| Societies (orders) | Nearest neighbour | OR-Tools, one trip | OR-Tools, two waves | Chained two trips | First solution |
|---|---|---|---|---|---|
| 100 (1,285) | 20 vans, ₹7.21, **over capacity** | 20 vans, ₹6.44, 100 % | **11 vans, ₹4.93, 100 %** | no on-time plan for 10–15 vans | 0.15 s |
| 200 (2,387) | 38 vans, ₹7.46, **over capacity** | 37 vans, ₹6.51, 100 % | **22 vans, ₹5.06, 100 %** | no on-time plan for 19–28 vans | 0.83 s |

- **Within the target:** one-trip OR-Tools stays well inside 30 s at 200 stops. Its 5 s and 30 s results are identical at every size.
- **Nearest neighbour:** it breaks crate capacity at 100 and 200 stops. It ignores the extra crate a donation takes, which shows why a checker is needed.

## Multi-trip: what worked and what did not
All plans are for one morning slot, whose windows (07–09, 08–10) overlap. A one-trip plan takes about 1.5 h a van, so each van sits idle for the second half of the morning.
- **Chained trips in one model** (each van as two vehicles; trip 2 starts after trip 1 ends plus reload):
  - At 50 stops it finds 6 vans.
  - At 100 and 200 stops, guided local search gets stuck in late plans: 85–88 % on time, and it does not even use all the vans it has. Cross-route side constraints are invisible to the routing library's fast move filters.
  - Adding "trip 2 only if trip 1 ran" stops every first-solution heuristic from finding any start.
- **Packing one-trip routes onto vans afterwards** saves 0–1 van: the routes all run at the same time.
- **Short trips (≤ 90 min), then packing** is worse: 11 / 22 / 43 vans. The solver has no reason to spread trips over the morning.
- **Two waves** works at every size:
  - first route the 07–09 societies;
  - then route the 08–10 societies with the same vans, each free from its return plus 20 min, and new vans only where needed.
  - Each wave is a plain CVRPTW, so it keeps the solver's speed, and the fleet falls by 40–45 %.
  - It is the pattern for M10 `/route/solve`.

## What this asks of others
- **Pilot data** (Rhythm Thakur, Manish Raje, with the hub manager):
  - the vehicle type, its capacity in kg and crates, and its real speed against OSRM car times (a few GPS traces);
  - how riders are paid (per shift or per hour);
  - how many orders fit a crate.
  - The ₹ figures above depend on these. The rankings between methods do not.
- **M9/M7:** captains' real windows. Two fixed windows make the wave split natural. Free-form windows would need waves by start time.
- **Hosting (ADR-0020 open item):** OSRM for the region needs about 100 MB of RAM and 400 MB of disk (including the source extract). It can sit next to the ML service.

## Not covered yet (planned in M7 and M10)
- live traffic (OSRM is free-flow; the × 1.5 factor is a guess to calibrate);
- the captain's last 50 m and failed handovers (M7 redelivery);
- the marginal insertion cost for green slots (M-H), which reuses this model;
- re-solving during the day when a van is late.

## Decision
The spike passed, so **no ADR change is needed**. OR-Tools CVRPTW with an OSRM matrix stays the routing approach (handbook 08 M-F).
- **Design adopted for M10 `/route/solve`:**
  - solve one window wave at a time, with vans carried over between waves;
  - a 10 s limit per wave (5 s was already enough here);
  - the independent checker on every plan before it is published;
  - the previous day's sequence as the fallback (M7).
- **Infra kept from this spike:** `infra/osrm/` and the compose "routing" profile.
