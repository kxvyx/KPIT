# KPIT K-IMPACT 2.0 — Decentralized Swarm Logistics (project notes)

Saved from chat on 3 Oct 2026. Deadline: **5 Oct 2026**.

**Problem Statement:** Decentralized Multi-Agent Coordination for Autonomous Logistics
**Themes covered:** Simulation · AI Systems · Energy · Mobility

---

## 1. The current system (what we improve on)

Today's warehouse robot fleets are **centralized**:

```
 Warehouse Management System (orders)
              │
 Central Fleet Manager  ← ONE server decides tasks, routes, traffic, charging
              │  Wi-Fi (must cover every corner)
   Robot 1  Robot 2  ...  Robot N  ← robots just follow instructions
```

1. Orders enter the WMS.
2. The central fleet manager collects every robot's position and battery over Wi-Fi.
3. It assigns each task, plans each route, manages traffic and schedules charging.
4. The robots execute its commands. Older AGVs follow magnetic tape or QR codes.

**Problems:** a single point of failure (server or Wi-Fi down means the whole fleet stops), a scalability bottleneck, heavy network dependency, slow reaction to local events, rigid setup, and vendor lock-in.

> *"Today's warehouse fleets depend on one central brain. If it fails, everything stops. We give every robot its own brain."*

---

## 2. Our idea

Remove the central controller. Each robot decides for itself and only talks to robots within 7 cells (short-range radio). Together they still act like a well-run fleet.

### Five features

1. **Auction-based task allocation (Contract Net Protocol).** Orders are announced at the docks and spread robot to robot by gossip. Each robot bids
   `cost = time to reach the pickup + work already queued + an energy penalty`, and the lowest bid wins. Every robot applies the same rule, so they all agree on the winner without a coordinator.
2. **Energy-aware bidding.** A robot refuses an order it can't finish while still keeping enough charge to reach a charger. Robots choose charging pads their neighbours aren't heading for, and switch pad only if a free one exists.
3. **Self-healing.** Robots exchange heartbeats. A robot is declared failed if it is silent while provably within radio range, or if another robot sees it within 3 cells (lidar/camera) and hears nothing. Its orders are re-auctioned, and a package it was carrying becomes a rescue order.
4. **Collision avoidance by local negotiation.** Robots share their next cell. Right of way goes to critical battery first, then a robot carrying a package, then the lower ID. Idle robots step aside, the robot giving way detours or backs off, and robots stuck for a long time take a random backoff step (as Ethernet does).
5. **Plug and play.** New robots join and start bidding immediately.

### Added 4 Oct 2026
6. **Separate order system (WMS), `swarm/orders.py`.** Creates orders with a weight (kg), a priority (normal or urgent, urgent due within 60 ticks) and readable places ("Dock 3 -> Rack B-12"). It only *publishes* orders to the dock beacons and never assigns robots, so it is not a central coordinator.
7. **Mixed fleet and payload.** Robots are light (max 10 kg, uses 20% less energy) or heavy (max 25 kg). A robot declines parcels heavier than it can lift. Carrying weight costs extra energy, and bids account for it. Heavy robots add a small penalty when bidding on light parcels, so they stay free for heavy work.
8. **Urgent orders.** Urgent orders are auctioned first and jump the winning robot's queue. A robot that is already busy bids higher, so a free robot usually wins.
9. **Energy safety.** Bids check energy for the queue in the order it will really be driven, with a 20% margin. Every 5 ticks a robot re-checks its queue, and if traffic has made a job unaffordable it hands back the orders it hasn't picked up (they're re-auctioned). Robots only head for pads they can reach, and only switch to a pad that's no farther away. Result: 0 robots ran out of battery in 48 stress runs (the old code had 4 in 24).
10. **Lossy radio (`msg_loss`).** Each heartbeat, gossip, beacon, bid and award can be lost. Lost bids are re-sent, a lost award triggers a re-auction, and a silent neighbour gets a direct probe before it's declared failed. In centralized mode only the server's commands are dropped (a lighter loss model).
11. **Order lifecycle and decline reasons.** Every order keeps a timeline (created, heard by, auction won by, picked up, robot failed, rescued, delivered, on time or late). Each robot that declines says why ("too heavy", "queue full", "battery 22% < 31% needed").

---

## 3. Tech stack

- **Python 3.14**, **pygame-ce** (demo window), standard library (`heapq`, `deque`, `random`, `time`), and **matplotlib/numpy** for the benchmark graphs.
- Algorithms: Contract Net Protocol, gossip, A* search, BFS distance maps, heartbeat failure detection, priority-based collision avoidance, random backoff, agent-based simulation.
- **No backend, by design.** A central backend would be the single point of failure we're removing. In a real deployment each robot runs the same code on its onboard computer (Raspberry Pi or Jetson) and communicates over ROS 2 (DDS), MQTT or a Wi-Fi mesh. An optional monitoring-only dashboard (Streamlit or FastAPI) could be added later.
- Future work: ROS 2 + Gazebo, then real robots, drones, or V2X vehicles.

---

## 4. Files

| File | Purpose |
|---|---|
| `swarm/core.py` | Swarm logic: map, robots, auctions, failure detection, energy, movement, the centralized baseline mode, and radio loss |
| `swarm/orders.py` | Order system (WMS): order creation, weight, priority, deadlines, place names, lifecycle history |
| `run_demo.py` | Live visual demo |
| `results/demo_screenshot.png` | Screenshot for the PPT |
| `benchmark.py` | Comparison graphs → `results/scalability.png`, `results/resilience.png`, `results/radio_loss.png` (`python benchmark.py`, about 10–15 min; `--quick` for a fast check) |

**Run:** `cd C:\Users\Nupur\kpit-swarm-logistics` then `python run_demo.py`. It opens fullscreen. Add `--window` to start in a window, or `--central` to start with the baseline.

**Demo screen (redesigned 4 Oct for non-technical viewers; view only, the simulation logic is unchanged):**
- **Header:** a clickable mode switch, **● DECENTRALIZED · NO CENTRAL COMPUTER** (blue) or **● CENTRALIZED · CENTRAL COMPUTER** (orange, red "DOWN" after a crash), plus the warehouse clock and radio status.
- **Parcels:** new parcels slide in through the dock wall and stack beside their dock (dimmed once a robot has claimed one). An urgent parcel pulses red when it arrives.
- **Robot card:** click a robot to see its status, battery, location, current order, distance, the offer it won with, its radio neighbours, and its live decision (offer or reason for declining) on every open order it knows about. It links to the parcel's story.
- **Map:**
  - Robots are labelled R1, R2…. Light robots are circles and heavy robots are bigger squares. A nose shows the driving direction, and a parcel drawn on top shows what a robot is carrying.
  - Docks D1–D5 show how many parcels are waiting. Racks, docks and chargers are labelled.
  - Green and red lines show the live auction for urgent parcels.
- **Status bubbles** (important events only): "Accepted #12", "Declined: too heavy", "R5 not responding", "Taking over #39", "Delivered #12" with a tick, "Low battery → charging".
- **Caption bar:** narrates the most important event in plain English.
- **Side panel:** 4 big result cards and a "What's happening" feed. Click a parcel or dock to see the parcel's story and who offered to take it.
- **Bottom bar** (hover over a button to see its shortcut): *What if…*: Break a robot (B, then click a robot; K breaks a random one), Rush hour (O), Add a robot (A), Bad radio (L), Crash central computer (X, central mode only). On the right: **Next scene** (N) and a **Controls** menu holding Pause (Space), Speed (Up/Down), Follow newest parcel (I), Technical details (T), Radio links (C), Planned paths (P), Fullscreen (F11) and Reset (R). Switch mode with M or the header.
- Esc closes the menu or a card, cancels break mode, leaves fullscreen, then quits.

**Demo script = presenter mode.** Press **N** (or the "Next scene" button) to step through:
0. Intro: robots with no central computer
1. A normal day (follows a parcel)
2. A robot breaks down (a robot carrying a parcel breaks; its parcel is followed through the rescue)
3. Rush hour
4. Bad radio (30% loss)
5. A new robot joins
6. The old way: one central computer
7. The central computer crashes: everything stops
8. The difference (summary). Pressing N again starts over.

---

## 5. Test results

| | Decentralized (ours) | Centralized |
|---|---|---|
| Throughput, 12–20 robots | Same | Same |
| Avg delivery time | about 3–10% slower | Faster |
| Failure detection | about 12 ticks | 1 tick |
| 60 forced robot failures over 20 runs | All detected, all orders recovered, 0 stuck | Same |
| Server crash | Not affected | Whole fleet stops |
| Load on the busiest node | about half of centralized (10 vs 20 msgs/tick at 20 robots); grows only because the fixed-size map gets more crowded | Equals fleet size (every robot talks to the server) |
| Deliveries after server crash (16 robots, benchmark) | 130 more | 6 more, then stops |

**Known limit (re-measured 4 Oct, after the energy fixes):** the 36×22 map with 6 chargers works well up to **30 robots** in both modes, with matching throughput and no battery failures. At **40 robots** the decentralized swarm breaks down: 116 orders pile up and about 14 robots per run run out of battery. Centralized still copes at 40 (about 4 battery failures). At **60 robots** both modes collapse. The bottleneck is the physical warehouse (6 chargers, aisle space), not the algorithm. A real deployment scales the floor and the chargers with the fleet. Hard limits: up to 605 robots can be placed at the start, and up to 116 can be added with the Add button (it needs free spots near the docks).

Bugs fixed during the build:
- livelock between two robots
- flapping pad choice
- undetected wrecks
- reopened orders being forgotten
- narrow-aisle gridlock
- detours being discarded
- charging-queue battery drain

---

## 6. Mapping to KPIT scoring

| Criterion | How we cover it |
|---|---|
| Innovation 25% | Self-healing plus energy-aware auctions, and provable wreck detection |
| Approach 25% | Established multi-robot methods combined and tested against a centralized baseline |
| Mobility 15% | Autonomous mobile robots; the same ideas apply to drones and V2V |
| Prototype 15% | Live interactive demo |
| Presentation 10% | Demo plus graphs |
| Scalability 10% | Plug-and-play robots, constant per-robot load, no server bottleneck |

## 7. Likely judge questions

- **Is the auction really decentralized?** Yes. Each robot computes its own bid, and all robots apply the same deterministic rule (lowest bid, ties broken by ID).
- **Can two robots take the same order?** No. There's exactly one winner per order, and reopened orders are tracked by their order ID.
- **Why is detection slower than centralized?** Only a nearby robot can detect a failure. That's the trade-off for having no server, and every order was still recovered.
- **Where's the backend?** Deliberately none. Each robot is its own backend.
- **Real-world use?** Warehouse robots, drone delivery swarms, and vehicle platoons using V2V.

## 8. About the guideline PDF

The PDF lists "Theme" (5 items) and "Problem Statement" (1 item) separately and doesn't say how they relate. The email calls the PS "Newly Introduced", which suggests it's an extra option alongside the themes. Our project is the PS and also covers 4 of the 5 themes, so it's safe under either reading. To be sure, ask the placement cell whether the PS is mandatory.

## 9. Remaining tasks

1. ~~`benchmark.py`~~ done 4 Oct.
2. README with setup steps.
3. PPT outline (title slide: PS + themes).
4. Backup screen-recording of the demo.
