"""
Order management: the warehouse order system (WMS) and the Order record.

The WMS is deliberately dumb about robots. It creates orders (what has to
move, from where, to where, how heavy, how urgent) and publishes them to
the dock beacons. It never decides which robot does the job: allocation
belongs to the swarm (or, in the baseline mode, to the central fleet
manager). So the WMS is not a coordinator and not a single point of
failure for the fleet: if it goes down, no new orders arrive, but every
order already published still gets delivered.
"""

NORMAL, URGENT = "normal", "urgent"


class Order:
    def __init__(self, oid, pickup, drop, t, weight=5, priority=NORMAL,
                 deadline=None, pickup_name="", drop_name="", beacon=True):
        self.id = oid
        self.pickup = pickup
        self.drop = drop
        self.pickup_name = pickup_name
        self.drop_name = drop_name
        self.weight = weight          # kg
        self.priority = priority      # normal / urgent
        self.deadline = deadline      # tick by which an urgent order is due
        self.created = t
        self.status = "open"          # open / assigned / delivered
        self.owner = None
        self.bids = {}                # robot id -> bid (INF = declined)
        self.reasons = {}             # robot id -> why it declined
        self.auction_start = None
        self.last_auction = []        # [(robot id, bid, reason)] of the last award
        self.heard = False            # has any robot heard about it yet
        self.beacon = beacon          # announced by a dock beacon
        self.reassigned = 0
        self.failed_at = None
        self.picked_at = None
        self.delivered_at = None
        self.history = [(t, f"Created by WMS: {weight} kg, {priority}, "
                            f"{pickup_name} -> {drop_name}")]

    @property
    def urgent(self):
        return self.priority == URGENT

    def note(self, t, msg):
        self.history.append((t, msg))

    def sort_key(self):
        """Urgent orders are auctioned first, then oldest first."""
        return (not self.urgent, self.created)


class OrderSystem:
    """Stand-in for the warehouse management system (WMS)."""

    def __init__(self, wh, rng, cfg):
        self.wh = wh
        self.rng = rng
        self.cfg = cfg
        self.next_id = 0
        # rack rows start at y = 3 and repeat every 4 cells (see Warehouse)
        self._rack_rows = list(range(3, wh.h - 3, 4))

    def place(self, cell):
        """Human-readable name of a cell: Dock 2, rack face B-12, Pad 3."""
        wh = self.wh
        if cell in wh.depots:
            return f"Dock {wh.depots.index(cell) + 1}"
        if cell in wh.pads:
            return f"Pad {wh.pads.index(cell) + 1}"
        x, y = cell
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            s = (x + dx, y + dy)
            if wh.is_shelf(s):
                for i, y0 in enumerate(self._rack_rows):
                    if s[1] in (y0, y0 + 1):
                        return f"Rack {chr(65 + i)}-{s[0] - 4}"
        return f"Floor ({x},{y})"

    def create(self, tick, dead=(), pickup=None, drop=None, urgent=None, weight=None):
        cfg, rng = self.cfg, self.rng
        pickup = pickup or rng.choice([d for d in self.wh.depots if d not in dead])
        drop = drop or rng.choice([d for d in self.wh.drops if d not in dead])
        if weight is None:
            # most parcels are light, some are pallets / heavy cartons
            weight = (rng.randint(1, cfg.light_payload) if rng.random() > cfg.heavy_order_fraction
                      else rng.randint(cfg.light_payload + 1, cfg.heavy_payload))
        if urgent is None:
            urgent = rng.random() < cfg.urgent_fraction
        o = Order(self.next_id, pickup, drop, tick, weight=weight,
                  priority=URGENT if urgent else NORMAL,
                  deadline=tick + cfg.urgent_deadline if urgent else None,
                  pickup_name=self.place(pickup), drop_name=self.place(drop))
        self.next_id += 1
        return o
