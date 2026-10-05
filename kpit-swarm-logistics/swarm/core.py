"""
Decentralized multi-agent coordination for autonomous warehouse logistics.

Headless simulation core (no graphics). Used by run_demo.py (Pygame view)
and benchmark.py (decentralized vs centralized experiments).

Key ideas
---------
* No central controller: every robot only talks to robots within
  `comm_radius` cells (like short-range V2V radio).
* Task allocation by auction (Contract Net Protocol): open orders are
  announced locally and spread by gossip; robots bid
  cost = ETA to pickup + workload + energy penalty, lowest bid wins.
* Energy-aware: a robot refuses (infinite bid) any order it cannot finish
  and still reach a charger. Robots pick charging pads that are less
  claimed by their neighbours.
* Self-healing: robots exchange heartbeats. If a neighbour goes silent
  while it is provably within radio range, it is declared failed and its
  orders (including the package it was carrying) are re-auctioned.
* Collision avoidance by local negotiation: robots share their next cell
  with neighbours; priority (carrying > waiting longer > id) resolves
  conflicts, idle robots step aside, deadlocks trigger local replanning.
* Orders come from a separate order system (swarm/orders.py) that only
  publishes them; it never assigns robots. Orders carry a weight and a
  priority. The fleet is mixed: light robots can't lift heavy parcels,
  carrying weight costs extra energy, urgent orders are auctioned first.
* Lossy radio: `msg_loss` drops each message (heartbeat, gossip, beacon,
  bid, award) independently, to test robustness like a real V2V link.
"""

import heapq
import random
import time
from collections import deque

from .orders import Order, OrderSystem

FREE, SHELF = 0, 1
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))
INF = float("inf")


def manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


class Config:
    def __init__(self, **kw):
        self.width = 36
        self.height = 22
        self.n_agents = 12
        self.comm_radius = 7          # cells (Manhattan)
        self.order_rate = 0.12        # expected new orders per tick
        self.step_energy = 0.35       # battery % per move
        self.idle_energy = 0.01
        self.charge_rate = 4.0
        self.low_battery = 30
        self.full_battery = 95
        self.queue_limit = 2          # active + queued orders per robot
        self.auction_window = 2       # ticks to collect bids
        self.heartbeat_timeout = 3    # ticks of silence before suspicion
        self.sensor_range = 3         # lidar/camera range for seeing a wreck
        self.seed = 7
        # orders and fleet mix
        self.urgent_fraction = 0.2    # share of orders that are urgent
        self.urgent_deadline = 60     # ticks to deliver an urgent order
        self.heavy_order_fraction = 0.3
        self.light_payload = 10       # kg a light robot can carry
        self.heavy_payload = 25       # kg a heavy robot can carry
        self.heavy_robot_fraction = 0.4
        self.light_energy = 0.8       # light robots use less energy per move
        self.load_energy = 0.5        # extra energy at full heavy payload
        # communication
        self.msg_loss = 0.0           # probability any single message is lost
        self.mode = "decentralized"   # or "centralized"
        for k, v in kw.items():
            if not hasattr(self, k):
                raise AttributeError(f"unknown config option {k}")
            setattr(self, k, v)


class Warehouse:
    """Static map: shelf racks, inbound docks (depots), charging pads."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.grid = [[FREE] * w for _ in range(h)]
        for y0 in range(3, h - 3, 4):
            for y in (y0, y0 + 1):
                if y >= h - 3:
                    continue
                for x in range(5, w - 6):
                    if (x - 5) % 7 == 6:      # cross aisle
                        continue
                    self.grid[y][x] = SHELF
        self.depots = [(1, y) for y in range(2, h - 1, 4)]
        self.pads = [(w - 2, 2), (w - 2, 3),
                     (w - 2, h // 2 - 1), (w - 2, h // 2),
                     (w - 2, h - 4), (w - 2, h - 3)]
        special = set(self.depots) | set(self.pads)
        self.drops = [
            (x, y) for y in range(h) for x in range(w)
            if self.grid[y][x] == FREE and (x, y) not in special
            and any(self.is_shelf((x + dx, y + dy)) for dx, dy in DIRS)
        ]
        self.free_cells = [(x, y) for y in range(h) for x in range(w)
                           if self.grid[y][x] == FREE]
        self._dist_cache = {}

    def is_shelf(self, c):
        x, y = c
        return 0 <= x < self.w and 0 <= y < self.h and self.grid[y][x] == SHELF

    def passable(self, c):
        x, y = c
        return 0 <= x < self.w and 0 <= y < self.h and self.grid[y][x] == FREE

    def neighbors(self, c):
        for dx, dy in DIRS:
            n = (c[0] + dx, c[1] + dy)
            if self.passable(n):
                yield n

    def dist_map(self, target):
        """BFS distances from every cell to `target` (cached)."""
        m = self._dist_cache.get(target)
        if m is None:
            m = {target: 0}
            q = deque([target])
            while q:
                c = q.popleft()
                d = m[c] + 1
                for n in self.neighbors(c):
                    if n not in m:
                        m[n] = d
                        q.append(n)
            self._dist_cache[target] = m
        return m

    def dist(self, a, b):
        return self.dist_map(b).get(a, 10 ** 6)

    def astar(self, start, goal, blocked):
        """Shortest path avoiding `blocked`; static BFS map is the heuristic."""
        if start == goal:
            return []
        h = self.dist_map(goal)
        if start not in h:
            return None
        heap = [(h[start], 0, start)]
        g = {start: 0}
        parent = {}
        while heap:
            _, gc, c = heapq.heappop(heap)
            if c == goal:
                path = []
                while c != start:
                    path.append(c)
                    c = parent[c]
                return path[::-1]
            if gc > g[c]:
                continue
            for n in self.neighbors(c):
                if n in blocked and n != goal:
                    continue
                ng = gc + 1
                if ng < g.get(n, 10 ** 9):
                    g[n] = ng
                    parent[n] = c
                    heapq.heappush(heap, (ng + h.get(n, 10 ** 6), ng, n))
        return None


class Agent:
    def __init__(self, aid, pos, battery, kind="light", payload=10, energy=1.0):
        self.id = aid
        self.kind = kind            # light / heavy
        self.payload = payload      # max parcel weight, kg
        self.energy = energy        # energy factor per move
        self.pos = pos
        self.battery = battery
        self.alive = True
        self.tasks = []             # Orders; tasks[0] is the active one
        self.carrying = None
        self.path = []
        self.goal = None
        self.path_goal = None       # goal the current path was planned for
        self.mode = "idle"          # idle/pickup/deliver/charge/charging/dead
        self.pad = None
        self.stage = None           # staging-lane spot while idle
        self.wait = 0
        self.stuck = 0              # ticks blocked since last move
        self.yield_cell = None
        self.last_seen = {}         # nid -> (tick, pos, pad, mode)
        self.known_orders = set()
        self.neighbors = []
        self.died_at = None
        self.delivered = 0
        self.compute_s = 0.0        # local decision time this tick


class Simulation:
    def __init__(self, cfg=None):
        self.cfg = cfg or Config()
        self.rng = random.Random(self.cfg.seed)
        self.wh = Warehouse(self.cfg.width, self.cfg.height)
        self.net_rng = random.Random(self.cfg.seed + 99991)   # radio losses only
        self.wms = OrderSystem(self.wh, self.rng, self.cfg)
        self.tick = 0
        self.agents = []
        self.orders = {}
        self.central_up = True
        self.known_dead = set()
        self.events = deque(maxlen=14)
        # metrics
        self.delivered = 0
        self.delivery_times = []
        self.delivered_series = [0]
        self.messages = 0
        self.msgs_series = []
        self.max_node_load_series = []
        self.compute_series = []      # decision ms per tick (bottleneck node)
        self.detect_latencies = []
        self.recovery_times = []
        self.reassigned = 0
        self.energy_used = 0.0
        self.urgent_times = []
        self.urgent_on_time = 0
        self.normal_times = []
        self.msgs_lost = 0
        self.suspicions = 0           # heartbeat timeouts (probes sent)
        self.released = 0             # orders handed back for lack of energy
        self._order_acc = 0.0
        self._nearest_pad = None
        special = set(self.wh.depots) | set(self.wh.pads)
        spots = [c for c in self.wh.free_cells if c not in special]
        self.rng.shuffle(spots)
        for i in range(self.cfg.n_agents):
            self.agents.append(self._new_agent(i, spots[i], self.rng.uniform(55, 100)))

    # ------------------------------------------------------------ helpers
    @property
    def alive_agents(self):
        return [a for a in self.agents if a.alive]

    def log(self, msg):
        self.events.appendleft(f"[t={self.tick}] {msg}")

    def dead_cells(self):
        return {a.pos for a in self.agents if not a.alive}

    def free_adjacent(self, cell):
        blocked = self.dead_cells() | {a.pos for a in self.agents}
        q, seen = deque([cell]), {cell}
        while q:
            c = q.popleft()
            for n in self.wh.neighbors(c):
                if n in seen:
                    continue
                if n not in blocked:
                    return n
                seen.add(n)
                q.append(n)
        return cell

    def pad_reserve(self, cell):
        """Distance from cell to the nearest working charging pad."""
        if self._nearest_pad is None:
            dead = self.dead_cells()
            m = {}
            for p in self.wh.pads:
                if p in dead:
                    continue
                for c, d in self.wh.dist_map(p).items():
                    if d < m.get(c, 10 ** 6):
                        m[c] = d
            self._nearest_pad = m
        return self._nearest_pad.get(cell, 10 ** 6)

    def _new_agent(self, aid, pos, battery):
        cfg = self.cfg
        # spread heavy robots evenly through the ids (e.g. 2 of every 5)
        heavy = int((aid + 1) * cfg.heavy_robot_fraction) > int(aid * cfg.heavy_robot_fraction)
        if heavy:
            return Agent(aid, pos, battery, "heavy", cfg.heavy_payload, 1.0)
        return Agent(aid, pos, battery, "light", cfg.light_payload, cfg.light_energy)

    def _sent(self):
        """True if a radio message gets through (lossy channel)."""
        if self.cfg.msg_loss <= 0 or self.net_rng.random() >= self.cfg.msg_loss:
            return True
        self.msgs_lost += 1
        return False

    def move_energy(self, a, weight=0):
        """Battery % used per cell moved, for this robot carrying `weight` kg."""
        cfg = self.cfg
        return cfg.step_energy * a.energy * (1 + cfg.load_energy * weight / cfg.heavy_payload)

    @staticmethod
    def tasks_with(a, o):
        """The robot's task queue if it accepts order o.
        Urgent orders jump the queue (right after a parcel already on board)."""
        if not o.urgent:
            return a.tasks + [o]
        i = 1 if a.carrying else 0
        return a.tasks[:i] + [o] + a.tasks[i:]

    def route(self, a, tasks=None):
        """Route through a task queue (default: the robot's own):
        length, end cell, energy."""
        cur, length, energy = a.pos, 0, 0.0
        for o in (a.tasks if tasks is None else tasks):
            if o is not a.carrying:
                d = self.wh.dist(cur, o.pickup)
                length += d
                energy += d * self.move_energy(a)
                cur = o.pickup
            d = self.wh.dist(cur, o.drop)
            length += d
            energy += d * self.move_energy(a, o.weight)
            cur = o.drop
        return length, cur, energy

    def bid_explain(self, a, o):
        """Energy-aware Contract-Net bid and, if declined (INF), the reason."""
        cfg = self.cfg
        if not a.alive:
            return INF, "offline"
        if o.weight > a.payload:
            return INF, f"too heavy ({o.weight} > {a.payload} kg)"
        if len(a.tasks) >= cfg.queue_limit:
            return INF, "queue full"
        if a.mode in ("charge", "charging") and a.battery < 70:
            return INF, "charging"
        # check energy for the queue in the order it would really be driven
        plan = self.tasks_with(a, o)
        i = plan.index(o)
        before, cur, _ = self.route(a, plan[:i])
        _, end, energy = self.route(a, plan)
        # 20% margin for detours and waiting in traffic, plus a fixed reserve
        need = 1.2 * (energy + self.pad_reserve(end) * self.move_energy(a)) + 5
        if a.battery < need:
            return INF, f"battery {a.battery:.0f}% < {need:.0f}% needed"
        spare = a.battery - need
        energy_penalty = 20 * max(0.0, (50 - spare) / 50)
        to_pick = self.wh.dist(cur, o.pickup)
        # an urgent job delays the work it jumps ahead of, so a free robot
        # beats a busy one that happens to be closer
        delayed = self.route(a)[0] - before if o.urgent else 0
        # a heavy robot is a scarce resource: don't waste it on light parcels
        # when a light robot can do the job almost as well
        reserve = 4 if a.payload > cfg.light_payload >= o.weight else 0
        return before + to_pick + 0.5 * delayed + energy_penalty + reserve, ""

    def bid(self, a, o):
        return self.bid_explain(a, o)[0]

    # ------------------------------------------------------------ actions
    def spawn_order(self, **kw):
        """The WMS publishes a new order to the dock beacons."""
        o = self.wms.create(self.tick, dead=self.dead_cells(), **kw)
        self.orders[o.id] = o
        return o

    def burst(self, n=15):
        for _ in range(n):
            self.spawn_order()
        self.log(f"Order burst: +{n} orders")

    def kill(self, aid):
        a = self.agents[aid]
        if not a.alive:
            return
        a.alive = False
        a.mode = "dead"
        a.died_at = self.tick
        self._nearest_pad = None
        # orders whose pickup/drop cell is now blocked by the wreck move aside
        for o in self.orders.values():
            if o.status != "delivered":
                if o.drop == a.pos:
                    o.drop = self.free_adjacent(a.pos)
                if o.pickup == a.pos and o is not a.carrying:
                    o.pickup = self.free_adjacent(a.pos)
        self.log(f"R{aid} FAILED (silent)")

    def add_agent(self):
        special = set(self.wh.depots) | set(self.wh.pads)
        taken = {a.pos for a in self.agents}
        spots = [c for c in self.wh.free_cells
                 if c not in special and c not in taken and c[0] <= 4]
        if not spots:
            return
        a = self._new_agent(len(self.agents), self.rng.choice(spots), 100.0)
        self.agents.append(a)
        self.log(f"R{a.id} ({a.kind}) joined the fleet (plug and play)")

    def crash_server(self):
        self.central_up = False
        self.log("CENTRAL SERVER DOWN")

    def handle_failure(self, aid, detector):
        a = self.agents[aid]
        self.known_dead.add(aid)
        self.detect_latencies.append(self.tick - a.died_at)
        n = len(a.tasks)
        for o in a.tasks:
            o.status = "open"
            o.owner = None
            o.bids = {}
            o.auction_start = None
            o.reassigned += 1
            o.failed_at = a.died_at
            by = f"R{detector.id}" if detector else "the server"
            if o is a.carrying:
                o.pickup = self.free_adjacent(a.pos)   # rescue the package
                o.pickup_name = f"wreck of R{aid}"
                o.picked_at = None
                o.beacon = False
                o.note(self.tick, f"R{aid} failed while carrying it; {by} "
                                  f"reopened it as a rescue order")
            else:
                o.note(self.tick, f"R{aid} failed; {by} reopened the order")
            if detector is not None:
                detector.known_orders.add(o.id)
        self.reassigned += n
        a.tasks, a.carrying = [], None
        who = f"R{detector.id}" if detector else "server"
        self.log(f"{who} detected R{aid} failure after "
                 f"{self.tick - a.died_at} ticks, re-auctioning {n} order(s)")

    # ------------------------------------------------------------ main loop
    def step(self):
        cfg = self.cfg
        self.tick += 1
        msgs = 0
        for a in self.agents:
            a.compute_s = 0.0

        # 1. new orders arrive at the docks
        self._order_acc += cfg.order_rate * self.rng.uniform(0.5, 1.5)
        while self._order_acc >= 1:
            self._order_acc -= 1
            self.spawn_order()

        alive = self.alive_agents
        open_orders = sorted((o for o in self.orders.values() if o.status == "open"),
                             key=Order.sort_key)

        if cfg.mode == "decentralized":
            msgs += self._decentralized_round(alive, open_orders)
        else:
            msgs += self._centralized_round(alive, open_orders)

        # 2. plan, negotiate moves, move
        self._move(alive)

        # 3. bookkeeping
        for a in alive:
            if a.mode == "charging":
                a.battery = min(100.0, a.battery + cfg.charge_rate)
            else:
                a.battery -= cfg.idle_energy
                self.energy_used += cfg.idle_energy
            if a.battery <= 0:
                a.battery = 0
                self.kill(a.id)
                self.log(f"R{a.id} ran out of battery")
        self.messages += msgs
        self.msgs_series.append(msgs)
        self.delivered_series.append(self.delivered)

    def _decentralized_round(self, alive, open_orders):
        cfg = self.cfg
        R = cfg.comm_radius
        msgs = 0
        # heartbeats: one local broadcast per robot, heard within radius
        for a in alive:
            a.neighbors = [b for b in alive if b is not a and manhattan(a.pos, b.pos) <= R]
        for a in alive:
            msgs += 1
            for b in a.neighbors:
                if self._sent():
                    b.last_seen[a.id] = (self.tick, a.pos, a.pad, a.mode)
        self.max_node_load_series.append(max((len(a.neighbors) for a in alive), default=0))

        # failure detection: silent although provably within range.
        # A suspect gets a direct probe (retried until acknowledged), so a
        # robot whose heartbeats were merely lost is not declared dead.
        for a in alive:
            t0 = time.perf_counter()
            for nid, (t, p, _, _) in list(a.last_seen.items()):
                if nid in self.known_dead:
                    continue
                silent = self.tick - t
                if silent >= cfg.heartbeat_timeout and manhattan(a.pos, p) + silent <= R:
                    self.suspicions += 1
                    if not self.agents[nid].alive:
                        self.handle_failure(nid, a)
                    msgs += 1
            a.compute_s += time.perf_counter() - t0

        # physical sensing: a robot body seen nearby that sends no heartbeat
        for w in self.agents:
            if w.alive or w.id in self.known_dead                     or self.tick - w.died_at < cfg.heartbeat_timeout:
                continue
            for a in alive:
                if manhattan(a.pos, w.pos) <= cfg.sensor_range:
                    self.handle_failure(w.id, a)
                    msgs += 1
                    break

        # order discovery: dock beacons + gossip piggybacked on heartbeats
        # (re-read open orders: failure handling above may have reopened some)
        open_orders = sorted((o for o in self.orders.values() if o.status == "open"),
                             key=Order.sort_key)
        open_ids = {o.id for o in open_orders}
        for o in open_orders:
            if o.beacon:
                for a in alive:
                    if manhattan(a.pos, o.pickup) <= R and o.id not in a.known_orders \
                            and self._sent():
                        a.known_orders.add(o.id)
        snapshot = {a.id: a.known_orders & open_ids for a in alive}
        for a in alive:
            for b in a.neighbors:
                new = snapshot[a.id] - b.known_orders
                if new and self._sent():
                    b.known_orders |= new
        knowers = {}
        for a in alive:
            a.known_orders &= open_ids
            for oid in a.known_orders:
                knowers.setdefault(oid, []).append(a)
        for o in open_orders:
            if not o.heard and o.id in knowers:
                o.heard = True
                how = "dock beacon" if o.beacon else "gossip"
                o.note(self.tick, f"Heard via {how} by "
                                  + ", ".join(f"R{a.id}" for a in knowers[o.id][:4]))

        # Contract Net auctions
        for o in open_orders:
            ks = knowers.get(o.id, [])
            for a in ks:
                if a.id not in o.bids:
                    msgs += 1
                    if not self._sent():
                        continue           # bid lost; the robot re-sends next tick
                    t0 = time.perf_counter()
                    o.bids[a.id], o.reasons[a.id] = self.bid_explain(a, o)
                    a.compute_s += time.perf_counter() - t0
            finite = [(c, aid) for aid, c in o.bids.items() if c < INF]
            if not finite:
                if self.tick % 5 == 0:
                    o.bids, o.reasons = {}, {}
                continue
            if o.auction_start is None:
                o.auction_start = self.tick
            if self.tick - o.auction_start >= cfg.auction_window:
                winner = None
                for c, aid in sorted(finite):
                    ag = self.agents[aid]
                    if ag.alive and self.bid(ag, o) < INF:
                        winner = ag
                        break
                if winner and not self._sent():
                    o.note(self.tick, f"Award to R{winner.id} lost on the radio; re-auction")
                    o.bids, o.reasons, o.auction_start = {}, {}, None
                    msgs += 1
                elif winner:
                    self._award(o, winner)
                    msgs += 1
                else:
                    o.bids, o.reasons, o.auction_start = {}, {}, None
        self.compute_series.append(1000 * max((a.compute_s for a in alive), default=0))
        return msgs

    def _centralized_round(self, alive, open_orders):
        msgs = 0
        if not self.central_up:
            self.max_node_load_series.append(0)
            self.compute_series.append(0)
            return 0
        msgs += len(alive)                     # every robot reports to server
        self.max_node_load_series.append(len(alive))
        for a in self.agents:
            if not a.alive and a.id not in self.known_dead:
                self.handle_failure(a.id, None)
        t0 = time.perf_counter()
        for o in open_orders:
            if o.status != "open":
                continue
            best, best_c = None, INF
            o.bids, o.reasons = {}, {}
            for a in alive:
                c, why = self.bid_explain(a, o)
                o.bids[a.id], o.reasons[a.id] = c, why
                if c < best_c:
                    best, best_c = a, c
            if best and not self._sent():
                o.note(self.tick, f"Server's command to R{best.id} lost; retry")
                msgs += 1
            elif best:
                o.heard = True
                self._award(o, best)
                msgs += 1
        self.compute_series.append(1000 * (time.perf_counter() - t0))
        return msgs

    def _award(self, o, winner):
        o.status = "assigned"
        o.owner = winner.id
        o.last_auction = sorted(((aid, c, o.reasons.get(aid, "")) for aid, c in o.bids.items()),
                                key=lambda r: (r[1], r[0]))
        n_bid = sum(1 for r in o.last_auction if r[1] < INF)
        who = "Server assigned" if self.cfg.mode == "centralized" else "Auction won by"
        o.note(self.tick, f"{who} R{winner.id} (bid {o.bids[winner.id]:.1f}); "
                          f"{n_bid} bid, {len(o.last_auction) - n_bid} declined")
        o.bids, o.reasons = {}, {}
        o.auction_start = None
        winner.tasks = self.tasks_with(winner, o)
        if o.failed_at is not None:
            self.recovery_times.append(self.tick - o.failed_at)
            self.log(f"Order #{o.id} recovered by R{winner.id}")
            o.failed_at = None

    # ------------------------------------------------------------ motion
    def _choose_pad(self, a, free_only=False):
        dead = self.dead_cells()
        if self.cfg.mode == "centralized":
            others = [(b.pad, b.mode) for b in self.alive_agents if b is not a]
        else:
            others = [(p, m) for (t, _, p, m) in a.last_seen.values()
                      if self.tick - t <= 1]
        live = [p for p in self.wh.pads if p not in dead]
        # never head for a pad the battery can't reach (with a 20% margin)
        reach = [p for p in live
                 if 1.2 * self.wh.dist(a.pos, p) * self.move_energy(a) + 1 < a.battery]
        if not reach:
            return min(live, key=lambda p: self.wh.dist(a.pos, p), default=None)
        best, best_s = None, INF
        for p in reach:
            if free_only and self._pad_taken(a, p):
                continue
            claims = sum(1 for (op, m) in others if op == p)
            claims += 2 * sum(1 for (op, m) in others if op == p and m == "charging")
            s = self.wh.dist(a.pos, p) + 25 * claims
            if s < best_s:
                best, best_s = p, s
        return best

    def _pad_taken(self, a, pad):
        """Pad is dead-blocked, or a robot this one can hear is charging on it."""
        if pad in self.dead_cells():
            return True
        if self.cfg.mode == "centralized":
            return any(b.pos == pad and b.mode == "charging"
                       for b in self.alive_agents if b is not a)
        return any(p == pad and m == "charging" and self.tick - t <= 1
                   for (t, _, p, m) in a.last_seen.values())

    def _recheck_energy(self, a):
        """Bids are checked against the battery when they are won, but traffic
        can stretch a route far beyond plan. Every few ticks a robot re-checks
        its queue; if it can no longer afford it, it hands back the orders it
        has not picked up yet (they are re-auctioned) and goes to charge."""
        if self.tick % 5 != a.id % 5 or not any(o is not a.carrying for o in a.tasks):
            return
        _, end, energy = self.route(a)
        if a.battery >= energy + self.pad_reserve(end) * self.move_energy(a) + 3:
            return
        keep = [a.carrying] if a.carrying else []
        for o in a.tasks:
            if o is a.carrying:
                continue
            o.status, o.owner = "open", None
            o.bids, o.reasons, o.auction_start = {}, {}, None
            o.reassigned += 1
            o.note(self.tick, f"R{a.id} handed it back: battery {a.battery:.0f}% "
                              f"no longer enough after traffic delays")
            a.known_orders.add(o.id)          # it gossips the order on
            self.released += 1
        a.tasks = keep
        self.log(f"R{a.id} handed back orders (battery {a.battery:.0f}%)")

    def _update_goal(self, a):
        cfg = self.cfg
        self._recheck_energy(a)
        if a.carrying:
            a.mode = "deliver"
            a.pad = a.stage = None
            return a.carrying.drop
        if a.tasks:
            a.mode = "pickup"
            a.pad = a.stage = None
            return a.tasks[0].pickup
        if a.mode == "charging":
            if a.battery >= cfg.full_battery:
                a.mode, a.pad = "idle", None
            else:
                return None
        if a.mode == "charge" or a.battery < cfg.low_battery:
            a.mode = "charge"
            a.stage = None
            if a.pad is None:
                a.pad = self._choose_pad(a)
            elif self._pad_taken(a, a.pad):
                # switch only to a pad that is actually free and no farther
                # away, else queue here: waiting costs almost no energy,
                # driving between far-apart pads drains the battery
                alt = self._choose_pad(a, free_only=True)
                if alt is not None and self.wh.dist(a.pos, alt) <= self.wh.dist(a.pos, a.pad):
                    a.pad = alt
            return a.pad
        a.mode = "idle"
        if a.yield_cell:
            return a.yield_cell
        # idle robots wait in the staging lane next to the inbound docks
        if a.pos[0] not in (3, 4):
            if a.stage is None:
                taken = {b.pos for b in self.agents} | {b.stage for b in self.agents}
                lane = [(x, y) for x in (3, 4) for y in range(self.wh.h)
                        if (x, y) not in taken and self.wh.passable((x, y))]
                a.stage = min(lane, key=lambda c: self.wh.dist(a.pos, c)) if lane else None
            return a.stage
        a.stage = None
        return None

    def _arrive(self, a):
        if a.carrying and a.pos == a.carrying.drop:
            o = a.carrying
            o.status = "delivered"
            a.tasks.remove(o)
            a.carrying = None
            a.delivered += 1
            self.delivered += 1
            took = self.tick - o.created
            o.delivered_at = self.tick
            self.delivery_times.append(took)
            msg = f"Delivered by R{a.id} to {o.drop_name} ({took} ticks after creation)"
            if o.urgent:
                self.urgent_times.append(took)
                on_time = self.tick <= o.deadline
                self.urgent_on_time += on_time
                msg += ", on time" if on_time else f", LATE by {self.tick - o.deadline}"
            else:
                self.normal_times.append(took)
            o.note(self.tick, msg)
        elif a.tasks and not a.carrying and a.pos == a.tasks[0].pickup:
            a.carrying = a.tasks[0]
            a.carrying.picked_at = self.tick
            a.carrying.note(self.tick, f"Picked up by R{a.id} at {a.carrying.pickup_name}")
        elif a.mode == "charge" and a.pos in self.wh.pads:
            a.mode, a.pad = "charging", a.pos
        if a.yield_cell == a.pos:
            a.yield_cell = None

    def _move(self, alive):
        dead = self.dead_cells()
        desired = {}
        for a in alive:
            self._arrive(a)
            goal = self._update_goal(a)
            a.goal = goal
            if goal is None or goal == a.pos:
                a.path = []
                continue
            if not a.path or a.path_goal != goal or a.path[0] in dead \
                    or manhattan(a.path[0], a.pos) != 1:
                a.path = self.wh.astar(a.pos, goal, dead) or []
                a.path_goal = goal
            if a.path:
                desired[a.id] = a.path[0]

        occupied = {a.pos: a for a in alive}
        prio = sorted(alive, key=lambda a: (a.carrying is None, -a.wait, a.id))
        claimed = set()
        resolved = set()
        pending = [a for a in prio if a.id in desired]
        for a in prio:
            if a.id not in desired:
                claimed.add(a.pos)
                resolved.add(a.id)
        # iterative passes so robots queued behind each other all advance
        progress = True
        while progress and pending:
            progress = False
            nxt = []
            for a in pending:
                t = desired[a.id]
                occ = occupied.get(t)
                if t in claimed or t in dead:
                    self._blocked(a, occ, claimed)
                    resolved.add(a.id)
                    progress = True
                elif occ is not None and occ.id not in resolved:
                    nxt.append(a)          # wait for occupant's decision
                elif occ is not None and occ.pos == t:
                    self._blocked(a, occ, claimed)
                    resolved.add(a.id)
                    progress = True
                else:
                    del occupied[a.pos]
                    a.pos = t
                    occupied[t] = a
                    a.path.pop(0)
                    e = self.move_energy(a, a.carrying.weight if a.carrying else 0)
                    a.battery -= e
                    self.energy_used += e
                    a.wait = a.stuck = 0
                    claimed.add(t)
                    resolved.add(a.id)
                    progress = True
            pending = nxt
        for a in pending:                  # circular waits: everyone holds
            self._blocked(a, occupied.get(desired[a.id]), claimed)

    @staticmethod
    def _rank(a):
        """Right-of-way: critical battery, then carrying a package, then lower id."""
        critical = a.mode == "charge" and a.battery < 15
        return (not critical, a.carrying is None, a.id)

    def _blocked(self, a, occ, claimed):
        claimed.add(a.pos)
        if occ is not None and occ.alive and occ.mode == "charging" and a.goal == occ.pos:
            return                         # queue politely for the pad
        a.wait += 1
        a.stuck += 1
        if occ is not None and occ.alive and occ.mode == "idle" and not occ.tasks:
            nxt = a.path[1] if len(a.path) > 1 else None
            free = [n for n in self.wh.neighbors(occ.pos)
                    if n != a.pos and n not in self.dead_cells()
                    and all(b.pos != n for b in self.agents)]
            options = [n for n in free if n != nxt] or free
            if options:
                occ.yield_cell = self.rng.choice(options)
            return
        # Only the lower-priority robot gives way, so two robots never
        # mirror each other's detours (livelock). Higher priority waits,
        # and replans itself only if it has been stuck for a long time.
        oncoming = occ is not None and occ.alive and occ.path
        gives_way = oncoming and self._rank(occ) < self._rank(a)
        if a.goal is None or not (gives_way and a.wait >= 1 or a.wait >= 6):
            return
        near = {b.pos for b in self.agents
                if b is not a and manhattan(a.pos, b.pos) <= 3}
        if oncoming:
            near |= set(occ.path[:3])
        near.discard(a.goal)
        dead = self.dead_cells()
        p = None if a.stuck >= 10 else self.wh.astar(a.pos, a.goal, near | dead)
        a.path_goal = a.goal              # keep the detour; don't snap back
        if p:
            a.path = p
        else:
            # no detour (narrow aisle / gridlock): back off into any free cell,
            # preferring cells off the other robot's route; random choice
            # breaks symmetric deadlocks (like Ethernet backoff)
            taken = {b.pos for b in self.agents}
            free = [n for n in self.wh.neighbors(a.pos) if n not in taken and n not in dead]
            side = [n for n in free if n not in near] or free
            if side:
                a.path = [self.rng.choice(side)]
        a.wait = 0

    # ------------------------------------------------------------ summary
    def summary(self, window=None):
        n = self.tick if window is None else min(window, self.tick)
        recent = self.delivered_series[-1] - self.delivered_series[-1 - n] if n else 0
        avg = lambda xs: sum(xs) / len(xs) if xs else 0.0
        return {
            "tick": self.tick,
            "delivered": self.delivered,
            "throughput_per_100": 100 * recent / n if n else 0.0,
            "avg_delivery_time": avg(self.delivery_times),
            "open_orders": sum(1 for o in self.orders.values() if o.status == "open"),
            "alive": len(self.alive_agents),
            "fleet": len(self.agents),
            "msgs_per_agent_tick": (avg(self.msgs_series[-n:]) / max(1, len(self.alive_agents))) if n else 0,
            "max_node_load": avg(self.max_node_load_series[-n:]) if n else 0,
            "compute_ms": avg(self.compute_series[-n:]) if n else 0,
            "detect_latency": avg(self.detect_latencies),
            "recovery_time": avg(self.recovery_times),
            "reassigned": self.reassigned,
            "urgent_delivery_time": avg(self.urgent_times),
            "normal_delivery_time": avg(self.normal_times),
            "urgent_on_time_pct": 100 * self.urgent_on_time / len(self.urgent_times)
                                  if self.urgent_times else 0.0,
            "energy_per_delivery": self.energy_used / self.delivered if self.delivered else 0.0,
            "msgs_lost": self.msgs_lost,
            "suspicions": self.suspicions,
            "released": self.released,
            "battery_failures": sum(1 for a in self.agents if not a.alive and a.battery <= 0),
        }
