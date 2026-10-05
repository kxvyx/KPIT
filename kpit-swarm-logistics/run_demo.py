"""
Live demo: Decentralized Swarm Logistics (KPIT K-IMPACT 2.0)

Run:  python run_demo.py              (fullscreen, decentralized)
      python run_demo.py --window     (start in a window)
      python run_demo.py --central    (start with the centralized baseline)

On screen: the "What if..." buttons, the Decentralized / Centralized switch
in the header, Next scene, and a Controls menu. Keys are shortcuts:

  B  break a robot (then click one)   K  break a random robot
  O  rush hour (+15 orders)           A  add a robot
  L  bad radio: 0/10/20/30/50 % lost  X  crash the central computer
  M  decentralized / centralized      N  next presenter scene
  SPACE pause   UP/DOWN speed   I follow a parcel   R reset
  F11 fullscreen   T technical details   C radio links   P paths
  Click a robot for its robot card, a parcel or dock for the parcel's story.
  Esc: cancel / close / leave fullscreen / quit

This file is only the view. All behaviour lives in swarm/core.py and
swarm/orders.py; the demo watches the simulation and explains it.
"""

import random
import re
import sys
import time
from collections import deque

import pygame

from swarm.core import INF, Config, Simulation, SHELF

# ---------------------------------------------------------------- palette
BG = (14, 17, 24)
PANEL_BG = (19, 23, 32)
CARD = (27, 32, 45)
BTN = (36, 42, 58)
BTN_HOVER = (52, 60, 82)
FLOOR = (27, 32, 46)
GRIDLINE = (33, 39, 55)
SHELF_C = (58, 66, 88)
SHELF_EDGE = (78, 88, 116)
DOCK = (40, 110, 200)
PAD = (220, 180, 40)
TEXT = (225, 230, 240)
MUTED = (140, 150, 170)
FAINT = (95, 105, 125)
ACCENT = (80, 200, 255)
GOOD = (70, 210, 120)
BAD = (240, 80, 80)
URGENT_C = (255, 70, 90)
CENTRAL_C = (255, 160, 60)
PARCEL = (196, 150, 98)
PARCEL_DIM = (130, 102, 72)
INK = (10, 12, 18)
MODE_COLORS = {
    "idle": (150, 155, 170),
    "pickup": (70, 190, 255),
    "deliver": (70, 210, 120),
    "charge": (240, 190, 40),
    "charging": (240, 190, 40),
    "dead": (240, 80, 80),
}
DOING = {"idle": "Waiting for a job", "pickup": "Driving to a pickup", "deliver": "Delivering",
         "charge": "Going to charge", "charging": "Charging", "dead": "Broken down"}
LOSS_STEPS = (0.0, 0.1, 0.2, 0.3, 0.5)
SPEEDS = ((4, "0.5x"), (8, "1x"), (16, "2x"), (32, "4x"))
CAPTION_SECS = 4.5
BUBBLE_SECS = 2.6
MAX_BUBBLES = 7
ARRIVE_SECS = 0.7

SCENES = [
    ("Warehouse robots with no central computer",
     "Each robot decides for itself which parcel to take. Watch them share the work."),
    ("1 · A normal day",
     "Parcels arrive at the docks on the left. Nearby robots make offers; the best "
     "offer wins. Nobody is in charge."),
    ("2 · A robot breaks down",
     "It can't call for help. A nearby robot notices the silence, and its parcel "
     "gets rescued by another robot."),
    ("3 · Rush hour",
     "15 orders at once. The robots split the work among themselves."),
    ("4 · Bad radio",
     "30% of all radio messages are lost. Robots re-send and keep working."),
    ("5 · A new robot joins",
     "Plug and play: no setup, it starts taking jobs right away."),
    ("6 · The old way: one central computer",
     "Now one central computer gives every robot its orders."),
    ("7 · The central computer crashes",
     "One failure, and the whole warehouse stops."),
    ("The difference",
     "The swarm has no single point of failure. Robots break, radio drops, rush hour "
     "hits, and the work still gets done."),
]


def R(aid):
    """Robots are shown as R1, R2, ... (internal ids start at 0)."""
    return f"R{aid + 1}"


def renumber(msg):
    return re.sub(r"\bR(\d+)\b", lambda m: R(int(m.group(1))), msg)


def clock(t):
    return f"{t // 60:02d}:{t % 60:02d}"


def order_color(oid):
    r = random.Random(oid * 7919)
    c = pygame.Color(0)
    c.hsva = (r.random() * 360, 60, 100, 100)
    return c


def short_reason(why):
    if why.startswith("too heavy"):
        return "too heavy"
    if why == "queue full":
        return "busy"
    if why == "charging":
        return "charging"
    if why.startswith("battery"):
        return "low battery"
    return None


PLAIN_RULES = [
    (r"Created by WMS: .*", "Order created by the warehouse system"),
    (r"Heard via dock beacon by (.*)", r"The dock announced it. Nearby robots heard: \1"),
    (r"Heard via gossip by (.*)", r"Robots passed the news on: \1"),
    (r"Auction won by (R\d+) \(bid [\d.]+\); (\d+) bid, (\d+) declined",
     r"\1 won the job (\2 offers, \3 declined)"),
    (r"Server assigned (R\d+) .*", r"Central computer gave the job to \1"),
    (r"Award to (R\d+) lost on the radio; re-auction",
     r"Message to \1 lost on the radio, robots decide again"),
    (r"Server's command to (R\d+) lost; retry", r"Command to \1 lost on the radio, retrying"),
    (r"(R\d+) failed while carrying it; the server reopened it as a rescue order",
     r"\1 broke down carrying it! Central computer sends a rescue"),
    (r"(R\d+) failed while carrying it; (R\d+) reopened it as a rescue order",
     r"\1 broke down carrying it! \2 noticed and called for a rescue"),
    (r"(R\d+) failed; the server reopened the order",
     r"\1 broke down before pickup. Central computer re-assigns it"),
    (r"(R\d+) failed; (R\d+) reopened the order",
     r"\1 broke down before pickup. \2 noticed and put the job back up"),
    (r"(R\d+) handed it back: .*", r"\1 returned the job: battery too low after traffic"),
    (r"Delivered by (R\d+) to (.*) \((\d+) ticks after creation\), on time",
     r"Delivered to \2 in \3 sec, on time"),
    (r"Delivered by (R\d+) to (.*) \((\d+) ticks after creation\), LATE by (\d+)",
     r"Delivered to \2 in \3 sec (\4 sec late)"),
    (r"Delivered by (R\d+) to (.*) \((\d+) ticks after creation\)",
     r"Delivered to \2 in \3 sec"),
]


def plain(msg):
    """Turn a lifecycle entry from the simulation into plain English."""
    m = renumber(msg)
    for pat, rep in PLAIN_RULES:
        if re.fullmatch(pat, m):
            return re.sub(pat, rep, m).replace("(1 offers", "(1 offer")
    return m


class Button:
    def __init__(self, label, action, key="", tip="", enabled=None, active=None,
                 danger=False, chevron=False):
        self.label = label            # str or callable -> str
        self.action = action
        self.key = key
        self.tip = tip                # str or callable -> str
        self.enabled = enabled or (lambda: True)
        self.active = active or (lambda: False)
        self.danger = danger
        self.chevron = chevron
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.flash = 0.0

    def text(self):
        return self.label() if callable(self.label) else self.label

    def tip_text(self):
        tip = self.tip() if callable(self.tip) else self.tip
        key = f"Shortcut: {self.key}" if self.key else ""
        return f"{tip}  ({key})" if tip and key else tip or key


class Demo:
    def __init__(self, mode, fullscreen=True, size=None):
        try:                           # crisp rendering on scaled Windows displays
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        pygame.init()
        pygame.display.set_caption("Decentralized Swarm Logistics - KPIT K-IMPACT 2.0")
        self.cfg_mode = mode
        self.loss = 0.0
        self.speed_i = 1
        self.paused = False
        self.show_links = True
        self.show_paths = False
        self.advanced = False
        self.menu_open = False
        self.scene = -1
        self.scene_shown = 0.0
        self.acc = 0.0
        self.frame = 0
        self.window_size = size or (1366, 768)
        self.link = None               # (rect, order id) of "show parcel story" link
        self.reset()
        self.make_buttons()
        self.set_display(fullscreen)
        self.clock = pygame.time.Clock()

    # ------------------------------------------------------------ state
    def reset(self, mode=None):
        if mode:
            self.cfg_mode = mode
        self.sim = Simulation(Config(mode=self.cfg_mode, seed=random.randint(0, 9999),
                                     msg_loss=self.loss))
        log = self.sim.log                     # listen to the simulation's event log
        self.sim.log = lambda msg: (log(msg), self.on_log(msg))
        self.selected = None           # followed order id
        self.robot_sel = None          # robot id shown in the robot card
        self.ostate = {}               # order id -> (status, picked_at, owner)
        self.next_oid = 0
        self.arrive_t = {}             # order id -> wall time its arrival animation starts
        self.amode = {}
        self.feed = deque(maxlen=40)
        self.bubbles = []              # [agent id, text, color, expires, prio, icon]
        self.caption = None            # (text, color, prio, until)
        self.break_mode = False

    def set_display(self, fullscreen):
        self.fullscreen = fullscreen
        if fullscreen:
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            self.screen = pygame.display.set_mode(self.window_size, pygame.RESIZABLE)
        self.layout()

    def layout(self):
        W, H = self.screen.get_size()
        wh = self.sim.wh
        self.s = s = max(0.7, min(W / 1366, H / 768, 1.8))
        self.pad = int(10 * s)
        self.panel_w = max(300, int(W * 0.25))
        self.header_h = int(52 * s)
        self.caption_h = int(50 * s)
        self.btn_h = int(36 * s)
        bar_h = self.btn_h + 2 * self.pad
        map_w = W - self.panel_w - 2 * self.pad
        map_h = H - self.header_h - self.caption_h - bar_h - 2 * self.pad
        self.cell = max(8, min(map_w // wh.w, map_h // wh.h))
        self.mx = self.pad + (map_w - self.cell * wh.w) // 2
        self.my = self.header_h + self.pad
        self.map_rect = pygame.Rect(self.mx, self.my, self.cell * wh.w, self.cell * wh.h)
        self.caption_rect = pygame.Rect(self.pad, self.map_rect.bottom + self.pad, map_w, self.caption_h)
        self.bar_rect = pygame.Rect(self.pad, self.caption_rect.bottom + self.pad // 2, map_w, bar_h)
        self.panel_rect = pygame.Rect(W - self.panel_w, 0, self.panel_w, H)

        def font(size, bold=False):
            return pygame.font.SysFont("segoeui", max(9, int(size * s)), bold=bold)
        self.f_title = font(21, True)
        self.f_head = font(16, True)
        self.f_body = font(14)
        self.f_bold = font(14, True)
        self.f_small = font(12)
        self.f_cap = font(17, True)
        self.f_card = font(26, True)
        self.f_robot = pygame.font.SysFont("segoeui", max(8, int(self.cell * 0.36)), bold=True)
        self.f_mono = pygame.font.SysFont("consolas", max(9, int(12 * s)))
        self.place_buttons()

    # ------------------------------------------------------------ buttons
    def make_buttons(self):
        central = lambda: self.sim.cfg.mode == "centralized"
        self.what_if = [
            Button("Break a robot", self.arm_break, "B",
                   "Then click any robot to make it break down",
                   active=lambda: self.break_mode, danger=True),
            Button("Rush hour", self.rush_hour, "O", "15 new orders arrive at once"),
            Button("Add a robot", self.add_robot, "A", "A new robot joins the fleet"),
            Button(lambda: f"Bad radio: {self.sim.cfg.msg_loss:.0%}", self.cycle_loss, "L",
                   "Lose 0 / 10 / 20 / 30 / 50 % of all radio messages",
                   active=lambda: self.sim.cfg.msg_loss > 0),
            Button("Crash central computer", self.crash, "X",
                   lambda: ("Take down the central computer" if central() else
                            "Decentralized mode has no central computer to crash"),
                   enabled=lambda: central() and self.sim.central_up, danger=True),
        ]
        self.right = [
            Button("Next scene", self.next_scene, "N", "Presenter mode: step through the story",
                   active=lambda: self.scene >= 0),
            Button("Controls", self.toggle_menu, "", "Speed, pause, reset, fullscreen, details",
                   active=lambda: self.menu_open, chevron=True),
        ]
        self.modes = [
            Button(lambda: "DECENTRALIZED · NO CENTRAL COMPUTER" if not central() else "DECENTRALIZED",
                   lambda: self.switch("decentralized"), "M",
                   lambda: "" if not central() else "Switch to robots deciding for themselves",
                   active=lambda: not central()),
            Button(lambda: ("CENTRALIZED" if not central() else
                            "CENTRALIZED · CENTRAL COMPUTER" if self.sim.central_up else
                            "CENTRALIZED · CENTRAL COMPUTER DOWN"),
                   lambda: self.switch("centralized"), "M",
                   lambda: "" if central() else "Switch to one computer deciding for every robot",
                   active=central),
        ]
        self.menu = [
            Button(lambda: "Play" if self.paused else "Pause", self.toggle_pause, "Space"),
            Button(lambda: f"Speed: {SPEEDS[self.speed_i][1]}", self.next_speed, "Up/Down"),
            Button("Follow the newest parcel", self.follow_newest, "I"),
            Button(lambda: "Hide technical details" if self.advanced else "Show technical details",
                   self.toggle_advanced, "T"),
            Button(lambda: "Hide radio links" if self.show_links else "Show radio links",
                   self.toggle_links, "C"),
            Button(lambda: "Hide planned paths" if self.show_paths else "Show planned paths",
                   self.toggle_paths, "P"),
            Button(lambda: "Leave fullscreen" if self.fullscreen else "Fullscreen",
                   lambda: self.set_display(not self.fullscreen), "F11"),
            Button("Reset the warehouse", lambda: self.reset(), "R"),
        ]
        self.buttons = self.what_if + self.right + self.modes

    def place_buttons(self):
        bar = self.bar_rect
        gap = int(6 * self.s)
        y = bar.y + self.pad
        x = bar.right
        for b in reversed(self.right):
            w = self.f_bold.size(b.text())[0] + int((40 if b.chevron else 28) * self.s)
            x -= w
            b.rect = pygame.Rect(x, y, w, self.btn_h)
            x -= gap
        right_edge = x - int(16 * self.s)
        x = bar.x + int(66 * self.s)
        natural = [self.f_bold.size(b.text())[0] + int(26 * self.s) for b in self.what_if]
        k = min(1.0, (right_edge - x - gap * (len(natural) - 1)) / sum(natural))
        for b, w in zip(self.what_if, natural):
            b.rect = pygame.Rect(x, y, int(w * k), self.btn_h)
            x += int(w * k) + gap
        # Controls menu pops up above its button
        ctl = self.right[-1].rect
        mw = int(250 * self.s)
        ih = int(self.btn_h * 0.92)
        top = ctl.top - 8 - ih * len(self.menu) - 12
        self.menu_rect = pygame.Rect(ctl.right - mw, top, mw, ih * len(self.menu) + 12)
        for i, b in enumerate(self.menu):
            b.rect = pygame.Rect(self.menu_rect.x + 6, top + 6 + i * ih, mw - 12, ih)

    def press(self, b):
        if b.enabled():
            b.flash = time.time() + 0.35
            b.action()

    KEYS = {pygame.K_b: "B", pygame.K_o: "O", pygame.K_a: "A", pygame.K_l: "L",
            pygame.K_x: "X", pygame.K_SPACE: "Space", pygame.K_i: "I", pygame.K_n: "N",
            pygame.K_r: "R", pygame.K_F11: "F11", pygame.K_t: "T", pygame.K_c: "C",
            pygame.K_p: "P"}

    # ------------------------------------------------------------ actions
    def arm_break(self):
        self.break_mode = not self.break_mode
        self.menu_open = False

    def break_robot(self, a):
        self.break_mode = False
        if a.carrying:
            self.show_order(a.carrying.id)
        self.sim.kill(a.id)

    def break_random(self):
        alive = self.sim.alive_agents
        busy = [a for a in alive if a.carrying] or alive
        if len(alive) > 1:
            self.break_robot(random.choice(busy))

    def rush_hour(self):
        self.sim.burst(15)

    def add_robot(self):
        self.sim.add_agent()

    def cycle_loss(self, value=None):
        cfg = self.sim.cfg
        if value is None:
            i = LOSS_STEPS.index(cfg.msg_loss) if cfg.msg_loss in LOSS_STEPS else 0
            value = LOSS_STEPS[(i + 1) % len(LOSS_STEPS)]
        cfg.msg_loss = self.loss = value
        if value == 0:
            self.event(3, "Radio is clear again: no messages lost.", ACCENT)
        else:
            self.event(3, f"Bad radio: {value:.0%} of all messages are now lost. "
                          "Robots re-send and keep working.", PAD)

    def crash(self):
        self.sim.crash_server()

    def switch(self, mode):
        if mode != self.sim.cfg.mode:
            self.reset(mode)

    def toggle_pause(self):
        self.paused = not self.paused

    def next_speed(self):
        self.speed_i = (self.speed_i + 1) % len(SPEEDS)

    def toggle_advanced(self):
        self.advanced = not self.advanced

    def toggle_links(self):
        self.show_links = not self.show_links

    def toggle_paths(self):
        self.show_paths = not self.show_paths

    def toggle_menu(self):
        self.menu_open = not self.menu_open

    def show_order(self, oid):
        self.selected, self.robot_sel, self.advanced = oid, None, False

    def show_robot(self, aid):
        self.robot_sel, self.selected, self.advanced = aid, None, False

    def follow_newest(self):
        live = [o for o in self.sim.orders.values() if o.status != "delivered"]
        pick = [o for o in live if o.urgent and o.status == "open"] or \
               [o for o in live if o.status == "open"] or live
        if pick:
            self.show_order(max(pick, key=lambda o: o.created).id)

    def next_scene(self):
        self.scene = (self.scene + 1) % len(SCENES)
        self.scene_shown = time.time()
        self.menu_open = False
        sc = self.scene
        if sc == 0:
            self.loss = 0.0
            self.speed_i = 1
            self.paused = False
            self.reset("decentralized")
        elif sc == 1:
            self.follow_newest()
        elif sc == 2:
            self.speed_i = 1
            self.break_random()
        elif sc == 3:
            self.selected = self.robot_sel = None
            self.rush_hour()
        elif sc == 4:
            self.cycle_loss(0.3)
        elif sc == 5:
            self.cycle_loss(0.0)
            self.add_robot()
        elif sc == 6:
            self.reset("centralized")
        elif sc == 7:
            self.crash()
        self.event(1, SCENES[sc][1], ACCENT, feed=False)

    # ------------------------------------------------------------ narration
    def event(self, prio, text, color, feed=True):
        now = time.time()
        if feed and prio >= 2:
            self.feed.appendleft((self.sim.tick, text, color))
        cap = self.caption
        if cap is None or now >= cap[3] or prio >= cap[2]:
            self.caption = (text, color, prio, now + CAPTION_SECS)

    def bubble(self, aid, text, color=TEXT, prio=1, icon=None):
        now = time.time()
        self.bubbles = [b for b in self.bubbles if b[3] > now and b[0] != aid]
        if len(self.bubbles) >= MAX_BUBBLES:
            low = min(self.bubbles, key=lambda b: (b[4], b[3]))
            if low[4] > prio:
                return
            self.bubbles.remove(low)
        self.bubbles.append([aid, text, color, now + BUBBLE_SECS, prio, icon])

    def kind(self, aid):
        return self.sim.agents[aid].kind

    def on_log(self, msg):
        """Narrate the simulation's own event log."""
        s = self.sim
        swarm = s.cfg.mode == "decentralized"
        m = re.match(r"R(\d+) FAILED", msg)
        if m:
            a = s.agents[int(m.group(1))]
            what = f", carrying parcel #{a.carrying.id}" if a.carrying else ""
            who = "A nearby robot has to notice." if swarm else "The central computer will notice."
            self.event(4, f"{R(a.id)} has broken down{what}. It can't call for help. {who}", BAD)
            return
        m = re.match(r"(?:R(\d+)|server) detected R(\d+) failure after (\d+) ticks, "
                     r"re-auctioning (\d+)", msg)
        if m:
            dead, secs, n = int(m.group(2)), m.group(3), int(m.group(4))
            jobs = f" Its {n} job{'s' if n != 1 else ''} go back up for grabs." if n else ""
            if m.group(1) is not None:
                det = int(m.group(1))
                self.bubble(det, f"{R(dead)} not responding", BAD, 3)
                self.event(4, f"{R(det)} noticed {R(dead)} stopped responding "
                              f"(after {secs} sec).{jobs}", GOOD)
            else:
                self.event(4, f"The central computer noticed {R(dead)} stopped.{jobs}", GOOD)
            return
        m = re.match(r"Order #(\d+) recovered by R(\d+)", msg)
        if m:
            oid, aid = int(m.group(1)), int(m.group(2))
            self.bubble(aid, f"Taking over #{oid}", GOOD, 3)
            self.event(4, f"{R(aid)} ({self.kind(aid)}) takes over parcel #{oid}. "
                          "Nothing gets lost.", GOOD)
            return
        m = re.match(r"R(\d+) \((\w+)\) joined", msg)
        if m:
            aid = int(m.group(1))
            self.bubble(aid, "Joined fleet · ready", ACCENT, 3)
            self.event(3, f"{R(aid)} ({m.group(2)}) joined the fleet. No setup needed: "
                          "it starts taking jobs right away.", ACCENT)
            return
        if msg.endswith("CENTRAL SERVER DOWN"):
            self.event(5, "The central computer has crashed! Robots get no more "
                          "instructions. Everything stops.", BAD)
            return
        m = re.match(r"Order burst: \+(\d+)", msg)
        if m:
            how = "The robots share them out." if swarm else "The central computer assigns them."
            self.event(3, f"Rush hour! {m.group(1)} new orders arrived at once. {how}", PAD)
            return
        m = re.match(r"R(\d+) ran out of battery", msg)
        if m:
            self.event(4, f"{R(int(m.group(1)))} ran out of battery.", BAD)

    def watch(self):
        """Compare the simulation with what we saw last tick and narrate changes."""
        s = self.sim
        swarm = s.cfg.mode == "decentralized"
        now = time.time()
        for i, oid in enumerate(range(self.next_oid, s.wms.next_id)):
            o = s.orders.get(oid)
            if o is None:
                continue
            self.ostate[oid] = ("new", None, None)
            self.arrive_t[oid] = now + 0.07 * i          # a burst rolls in one by one
            if o.urgent:
                self.event(2, f"New URGENT parcel #{o.id} ({o.weight} kg) at {o.pickup_name}: "
                              f"must reach {o.drop_name} within {o.deadline - o.created} sec.",
                           URGENT_C)
        self.next_oid = s.wms.next_id
        for oid, (st, picked, owner) in list(self.ostate.items()):
            o = s.orders[oid]
            if (o.status, o.picked_at, o.owner) == (st, picked, owner):
                continue
            important = o.urgent or oid == self.selected
            if st in ("open", "new") and o.status in ("assigned", "delivered") and o.owner is not None:
                self.on_award(o, important, swarm)
            elif st == "assigned" and o.status == "open" and owner is not None \
                    and "handed it back" in o.history[-1][1]:
                self.bubble(owner, f"Returned #{oid} · low battery", PAD, 3)
                self.event(3, f"{R(owner)} returned parcel #{oid}: traffic used more battery "
                              "than planned. Another robot will take it.", PAD)
            if o.picked_at and o.picked_at != picked and oid == self.selected:
                self.event(2, f"{R(o.owner)} picked up parcel #{oid} at {renumber(o.pickup_name)}.",
                           TEXT)
            if o.status == "delivered":
                took = o.delivered_at - o.created
                if important:
                    self.bubble(o.owner, f"Delivered #{oid}", GOOD, 2, icon="check")
                    if o.urgent:
                        late = o.delivered_at - o.deadline
                        txt = "on time" if late <= 0 else f"{late} sec late"
                        self.event(2, f"Urgent parcel #{oid} delivered to {o.drop_name} "
                                      f"in {took} sec, {txt}.", GOOD if late <= 0 else PAD)
                    else:
                        self.event(2, f"Parcel #{oid} delivered to {o.drop_name} in {took} sec.", GOOD)
                del self.ostate[oid]
                self.arrive_t.pop(oid, None)
                continue
            self.ostate[oid] = (o.status, o.picked_at, o.owner)
        for a in s.agents:
            prev = self.amode.get(a.id)
            if a.alive and a.mode == "charge" and prev not in ("charge", "charging", None):
                self.bubble(a.id, "Low battery → charging", PAD, 1)
            self.amode[a.id] = a.mode

    def on_award(self, o, important, swarm):
        w = o.owner
        offers = sum(1 for _, c, _ in o.last_auction if c < INF)
        declines = [(aid, short_reason(why)) for aid, c, why in o.last_auction
                    if c == INF and short_reason(why)]
        rescue = o.reassigned > 0
        if important or rescue:
            self.bubble(w, f"Taking over #{o.id}" if rescue else f"Accepted #{o.id}", GOOD, 2)
            if swarm:
                for aid, why in declines[:2]:
                    self.bubble(aid, f"Declined: {why}", MUTED, 1)
        handed_back = len(o.history) >= 2 and "handed it back" in o.history[-2][1]
        if rescue and handed_back:
            self.event(3, f"{R(w)} ({self.kind(w)}) takes over parcel #{o.id}.", GOOD)
        elif important and not rescue:
            label = "urgent parcel" if o.urgent else "parcel"
            if swarm:
                why = f" {R(declines[0][0])} declined: {declines[0][1]}." if declines else ""
                self.event(2, f"{R(w)} ({self.kind(w)}) accepted {label} #{o.id}: the best of "
                              f"{offers} offer{'s' if offers != 1 else ''}.{why}", GOOD)
            else:
                self.event(2, f"The central computer gave {label} #{o.id} to {R(w)}.", CENTRAL_C)

    # ------------------------------------------------------------ input
    def cell_at(self, pos):
        x, y = pos
        if not self.map_rect.collidepoint(x, y):
            return None
        return ((x - self.mx) // self.cell, (y - self.my) // self.cell)

    def robot_at(self, c, alive=True):
        for a in self.sim.agents:
            if a.pos == c and (a.alive or not alive):
                return a
        return None

    def dock_near(self, c):
        """A dock, or the parcel area beside it, was clicked."""
        for d in self.sim.wh.depots:
            if c[0] <= d[0] and abs(c[1] - d[1]) <= 1:
                return d
        return None

    def at_pickup(self, o):
        """Parcel is physically waiting at its pickup point."""
        return o.status != "delivered" and o.picked_at is None

    def order_at(self, c):
        live = [o for o in self.sim.orders.values() if o.status != "delivered"]
        live.sort(key=lambda o: (not o.urgent, o.created))
        d = self.dock_near(c)
        if d is not None:
            here = [o for o in live if o.pickup == d and self.at_pickup(o)]
            return here[0].id if here else None
        for o in live:
            if (self.at_pickup(o) and o.pickup == c) or o.drop == c:
                return o.id
        return None

    def handle(self, e):
        if e.type == pygame.QUIT:
            return False
        if e.type in (pygame.VIDEORESIZE, pygame.WINDOWSIZECHANGED) and not self.fullscreen:
            self.screen = pygame.display.get_surface()
            self.window_size = self.screen.get_size()
            self.layout()
        if e.type == pygame.MOUSEBUTTONDOWN and e.button in (1, 3):
            if self.menu_open:
                for b in self.menu:
                    if b.rect.collidepoint(e.pos):
                        self.press(b)
                        return True
                if not self.right[-1].rect.collidepoint(e.pos):
                    self.menu_open = False
                    return True
            for b in self.buttons:
                if b.rect.collidepoint(e.pos):
                    self.press(b)
                    return True
            if self.link and self.link[0].collidepoint(e.pos):
                self.show_order(self.link[1])
                return True
            c = self.cell_at(e.pos)
            if c is None:
                return True
            a = self.robot_at(c) or self.robot_at(c, alive=False)
            if self.break_mode:
                if a and a.alive:
                    self.break_robot(a)
                else:
                    self.break_mode = False
                return True
            if a:
                self.show_robot(a.id)
            else:
                self.selected, self.robot_sel = self.order_at(c), None
                if self.selected is not None:
                    self.advanced = False
        if e.type == pygame.KEYDOWN:
            k = e.key
            if k == pygame.K_ESCAPE:
                if self.menu_open:
                    self.menu_open = False
                elif self.break_mode:
                    self.break_mode = False
                elif self.selected is not None or self.robot_sel is not None:
                    self.selected = self.robot_sel = None
                elif self.fullscreen:
                    self.set_display(False)
                else:
                    return False
            elif k == pygame.K_UP:
                self.speed_i = min(len(SPEEDS) - 1, self.speed_i + 1)
            elif k == pygame.K_DOWN:
                self.speed_i = max(0, self.speed_i - 1)
            elif k == pygame.K_k:
                self.break_random()
            elif k == pygame.K_m:
                self.switch("centralized" if self.sim.cfg.mode == "decentralized" else "decentralized")
            elif k in self.KEYS:
                b = next((b for b in self.buttons + self.menu if b.key == self.KEYS[k]), None)
                if b:
                    self.press(b)
        return True

    # ------------------------------------------------------------ drawing helpers
    def px(self, c):
        return (self.mx + c[0] * self.cell + self.cell // 2,
                self.my + c[1] * self.cell + self.cell // 2)

    def text(self, s, font, color, pos, anchor="topleft"):
        img = font.render(s, True, color)
        r = img.get_rect(**{anchor: pos})
        self.screen.blit(img, r)
        return r

    @staticmethod
    def wrap(s, font, width):
        lines, cur = [], ""
        for w in s.split():
            t = f"{cur} {w}".strip()
            if font.size(t)[0] <= width or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    def bolt(self, center, size, color, surf=None):
        cx, cy = center
        k = size / 7
        pts = [(cx + 2 * k, cy - 7 * k), (cx - 4 * k, cy + k), (cx, cy + k),
               (cx - 2 * k, cy + 7 * k), (cx + 4 * k, cy - k), (cx, cy - k)]
        pygame.draw.polygon(surf or self.screen, color, pts)

    @staticmethod
    def check(surf, x, y, size, color, width=2):
        pygame.draw.lines(surf, color, False, [(x, y + size * 0.55), (x + size * 0.38, y + size * 0.9),
                                               (x + size, y + size * 0.1)], width)

    def parcel_icon(self, center, size, urgent=False, color=PARCEL):
        r = pygame.Rect(0, 0, size, size)
        r.center = center
        rad = max(1, size // 6)
        pygame.draw.rect(self.screen, color, r, border_radius=rad)
        pygame.draw.line(self.screen, (120, 85, 50), (r.centerx, r.top + 1), (r.centerx, r.bottom - 2), 1)
        pygame.draw.rect(self.screen, URGENT_C if urgent else INK, r, 2 if urgent else 1, border_radius=rad)

    def draw_button(self, b, fill=None, fg=None, align="center"):
        now = time.time()
        on, active, hover = b.enabled(), b.active(), b.rect.collidepoint(pygame.mouse.get_pos())
        rad = int(7 * self.s)
        if fill is None:
            if not on:
                fill, fg = (24, 28, 38), FAINT
            elif b.flash > now or active:
                fill, fg = (BAD if b.danger else ACCENT), INK
            elif hover:
                fill, fg = BTN_HOVER, TEXT
            else:
                fill, fg = BTN, TEXT
        pygame.draw.rect(self.screen, fill, b.rect, border_radius=rad)
        if b.danger and on and not active and b.flash <= now:
            pygame.draw.rect(self.screen, BAD, b.rect, 1, border_radius=rad)
        if align == "left":
            self.text(b.text(), self.f_bold, fg, (b.rect.x + int(12 * self.s), b.rect.centery), "midleft")
            if b.key:
                self.text(b.key, self.f_small, FAINT if fg != INK else (40, 50, 70),
                          (b.rect.right - int(10 * self.s), b.rect.centery), "midright")
        elif b.chevron:
            r = self.text(b.text(), self.f_bold, fg, (b.rect.centerx - int(7 * self.s), b.rect.centery),
                          "center")
            cx, cy, k = r.right + int(9 * self.s), b.rect.centery, int(4 * self.s)
            up = self.menu_open
            pts = [(cx - k, cy + (k // 2 if up else -k // 2)), (cx, cy + (-k // 2 if up else k // 2)),
                   (cx + k, cy + (k // 2 if up else -k // 2))]
            pygame.draw.lines(self.screen, fg, False, pts, 2)
        else:
            self.text(b.text(), self.f_bold, fg, b.rect.center, "center")

    # ------------------------------------------------------------ map
    def draw_map(self):
        wh, C = self.sim.wh, self.cell
        for y in range(wh.h):
            for x in range(wh.w):
                r = pygame.Rect(self.mx + x * C, self.my + y * C, C, C)
                if wh.grid[y][x] == SHELF:
                    pygame.draw.rect(self.screen, SHELF_C, r)
                    pygame.draw.rect(self.screen, SHELF_EDGE, r, 1)
                else:
                    pygame.draw.rect(self.screen, FLOOR, r)
                    pygame.draw.rect(self.screen, GRIDLINE, r, 1)
        lane = pygame.Rect(self.mx + 3 * C, self.my, 2 * C, wh.h * C)
        surf = pygame.Surface(lane.size, pygame.SRCALPHA)
        surf.fill((80, 200, 255, 14))
        self.screen.blit(surf, lane.topleft)
        z = self.f_small
        self.text("DOCKS", z, FAINT, (self.mx + C, self.my + C // 2), "center")
        self.text("WAITING", z, FAINT, (self.mx + 4 * C, self.my + (wh.h - 1) * C + C // 2), "center")
        self.text("CHARGERS", z, FAINT, (self.mx + (wh.w - 2) * C, self.my + C // 2), "midright")
        for i, y0 in enumerate(range(3, wh.h - 3, 4)):
            self.text(f"RACK {chr(65 + i)}", z, FAINT, (self.mx + 5 * C + 2, self.my + y0 * C - C // 2),
                      "midleft")
        for i, d in enumerate(wh.depots):
            r = pygame.Rect(self.mx + d[0] * C + 2, self.my + d[1] * C + 2, C - 4, C - 4)
            pygame.draw.rect(self.screen, DOCK, r, border_radius=4)
            self.text(f"D{i + 1}", self.f_robot, TEXT, r.center, "center")
        for p in wh.pads:
            r = pygame.Rect(self.mx + p[0] * C + 3, self.my + p[1] * C + 3, C - 6, C - 6)
            pygame.draw.rect(self.screen, PAD, r, 2, border_radius=4)
            self.bolt(r.center, C * 0.35, PAD)

    def draw_parcels(self):
        """Parcels waiting at the docks (sliding in as they arrive) and at wrecks."""
        s, C, now = self.sim, self.cell, time.time()
        wh = s.wh
        waiting = {d: [] for d in wh.depots}
        for o in s.orders.values():
            if self.at_pickup(o):
                if o.pickup in waiting:
                    waiting[o.pickup].append(o)
                else:                                   # rescue: lying at a wreck
                    self.parcel_icon(self.px(o.pickup), max(6, int(C * 0.5)), o.urgent)
        box = max(6, int(C * 0.42))
        row_order = (2, 3, 1, 4, 0, 5)                  # fill next to the dock first
        self.screen.set_clip(self.map_rect)
        for d, orders in waiting.items():
            orders.sort(key=lambda o: o.created)
            top = self.my + (d[1] - 1) * C
            for i, o in enumerate(orders[:12]):
                col, row = i % 2, row_order[i // 2]
                tx = self.mx + int(C * 0.27) + int(col * C * 0.46)
                ty = top + int(C * 0.27) + int(row * C * 0.49)
                t0 = self.arrive_t.get(o.id, 0)
                if now < t0:
                    continue
                p = min(1.0, (now - t0) / ARRIVE_SECS)
                ease = 1 - (1 - p) ** 3
                x = self.mx - 2 * C + (tx - (self.mx - 2 * C)) * ease
                color = PARCEL if o.status == "open" else PARCEL_DIM     # dim = claimed
                self.parcel_icon((int(x), ty), box, o.urgent, color)
                if o.urgent and now - t0 < 1.6:
                    ring = int(box * (0.8 + 1.4 * ((now - t0) % 0.8) / 0.8))
                    pygame.draw.circle(self.screen, URGENT_C, (int(x), ty), ring, 2)
            extra = len(orders) - 12
            if extra > 0:
                self.text(f"+{extra}", self.f_small, TEXT, (self.mx + C // 2, top + 3 * C), "midtop")
        self.screen.set_clip(None)

    def draw_orders(self):
        C = self.cell
        sel = self.sim.orders.get(self.selected)
        for o in self.sim.orders.values():
            if o.status == "delivered":
                continue
            cx, cy = self.px(o.drop)
            k = max(3, C // 4)
            pts = [(cx, cy - k), (cx + k, cy), (cx, cy + k), (cx - k, cy)]
            pygame.draw.polygon(self.screen, URGENT_C if o.urgent else order_color(o.id), pts,
                                0 if o.urgent else 2)
        # live auction: who is making offers for urgent / followed parcels
        if self.sim.cfg.mode == "decentralized":
            surf = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
            for o in self.sim.orders.values():
                if o.status != "open" or not o.bids or not (o.urgent or o is sel):
                    continue
                for aid, c in o.bids.items():
                    a = self.sim.agents[aid]
                    if a.alive:
                        col = (70, 210, 120, 150) if c < INF else (240, 80, 80, 90)
                        pygame.draw.line(surf, col, self.px(a.pos), self.px(o.pickup), 2)
            self.screen.blit(surf, (0, 0))
        if sel is not None and sel.status != "delivered":
            spots = [(sel.drop, "deliver")]
            if sel.picked_at is None:
                spots.insert(0, (sel.pickup, "pick up"))
            for c, label in spots:
                r = pygame.Rect(0, 0, C + 6, C + 6)
                r.center = self.px(c)
                pygame.draw.rect(self.screen, (255, 255, 255), r, 2, border_radius=6)
                self.text(label, self.f_small, TEXT, (r.centerx, r.top - 1), "midbottom")
            start = self.px(sel.pickup) if sel.picked_at is None or sel.owner is None \
                else self.px(self.sim.agents[sel.owner].pos)
            pygame.draw.line(self.screen, (255, 255, 255), start, self.px(sel.drop), 1)

    def draw_links(self):
        s = self.sim
        surf = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        if s.cfg.mode == "decentralized":
            for a in s.alive_agents:
                for b in a.neighbors:
                    if a.id < b.id and b.alive:
                        pygame.draw.line(surf, (80, 200, 255, 38), self.px(a.pos), self.px(b.pos), 1)
        elif s.central_up:
            hub = self.modes[1].rect.midbottom
            for a in s.alive_agents:
                pygame.draw.line(surf, (255, 160, 60, 32), hub, self.px(a.pos), 1)
        self.screen.blit(surf, (0, 0))

    def draw_agents(self):
        s, C = self.sim, self.cell
        hover = self.cell_at(pygame.mouse.get_pos())
        sel = s.orders.get(self.selected)
        for a in s.agents:
            cx, cy = self.px(a.pos)
            col = MODE_COLORS[a.mode]
            route = [self.px(a.pos)] + [self.px(c) for c in a.path]
            if self.show_paths and a.alive and a.path:
                pygame.draw.lines(self.screen, col, False, route, 1)
            if (a.pos == hover or a.id == self.robot_sel) and a.alive:
                if s.cfg.mode == "decentralized":
                    self.draw_radius(a)
                if a.path:
                    pygame.draw.lines(self.screen, col, False, route, 2)
            heavy = a.kind == "heavy"
            half = int(C * (0.47 if heavy else 0.36))
            if not a.alive:
                pygame.draw.circle(self.screen, (90, 30, 30), (cx, cy), half)
                k = int(half * 0.6)
                pygame.draw.line(self.screen, BAD, (cx - k, cy - k), (cx + k, cy + k), 3)
                pygame.draw.line(self.screen, BAD, (cx - k, cy + k), (cx + k, cy - k), 3)
                if a.id not in s.known_dead and self.frame % 30 < 15:
                    pygame.draw.circle(self.screen, BAD, (cx, cy), half + 4, 2)
                if a.id == self.robot_sel:
                    pygame.draw.circle(self.screen, (255, 255, 255), (cx, cy), half + 6, 2)
                continue
            if a.mode == "idle":
                col = tuple(int(v * 0.75) for v in col)
            if a.path:                    # driving direction: a nose towards the next cell
                dx, dy = a.path[0][0] - a.pos[0], a.path[0][1] - a.pos[1]
                if abs(dx) + abs(dy) == 1:
                    tip = (cx + dx * (half + C * 0.22), cy + dy * (half + C * 0.22))
                    sx, sy = -dy * half * 0.55, dx * half * 0.55
                    bx, by = cx + dx * half * 0.8, cy + dy * half * 0.8
                    pygame.draw.polygon(self.screen, col, [tip, (bx + sx, by + sy), (bx - sx, by - sy)])
            if a.mode == "charging" and self.frame % 20 < 10:
                pygame.draw.circle(self.screen, PAD, (cx, cy), half + 3, 2)
            if heavy:
                body = pygame.Rect(0, 0, 2 * half, 2 * half)
                body.center = (cx, cy)
                pygame.draw.rect(self.screen, col, body, border_radius=max(2, C // 6))
                pygame.draw.rect(self.screen, INK, body, 1, border_radius=max(2, C // 6))
            else:
                pygame.draw.circle(self.screen, col, (cx, cy), half)
                pygame.draw.circle(self.screen, INK, (cx, cy), half, 1)
            self.text(R(a.id), self.f_robot, INK, (cx, cy), "center")
            if a.carrying:
                self.parcel_icon((cx + half - 1, cy - half + 1), max(6, int(C * 0.36)),
                                 a.carrying.urgent, order_color(a.carrying.id))
            if a.mode in ("charge", "charging"):
                self.bolt((cx - half, cy - half), C * 0.22, PAD)
            if (sel is not None and sel.owner == a.id and sel.status == "assigned") or a.id == self.robot_sel:
                pygame.draw.circle(self.screen, (255, 255, 255), (cx, cy), half + 5, 2)
            bw = int(C * 0.8)
            bx, by = cx - bw // 2, cy + half + 2
            pygame.draw.rect(self.screen, (40, 40, 50), (bx, by, bw, 3))
            bc = GOOD if a.battery > 50 else PAD if a.battery > 25 else BAD
            pygame.draw.rect(self.screen, bc, (bx, by, int(bw * max(0, a.battery) / 100), 3))

    def draw_radius(self, a):
        Rr = self.sim.cfg.comm_radius * self.cell
        surf = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        cx, cy = self.px(a.pos)
        pts = [(cx, cy - Rr), (cx + Rr, cy), (cx, cy + Rr), (cx - Rr, cy)]
        pygame.draw.polygon(surf, (80, 200, 255, 22), pts)
        pygame.draw.polygon(surf, (80, 200, 255, 120), pts, 1)
        self.screen.blit(surf, (0, 0))

    def draw_bubbles(self):
        now = time.time()
        self.bubbles = [b for b in self.bubbles if b[3] > now]
        placed = []
        for aid, text, color, until, _, icon in sorted(self.bubbles, key=lambda b: -b[4]):
            cx, cy = self.px(self.sim.agents[aid].pos)
            img = self.f_small.render(text, True, INK)
            ih = img.get_height()
            iw = ih + 2 if icon else 0
            w, h = img.get_width() + iw + 16, ih + 6
            r = pygame.Rect(0, 0, w, h + 6)
            r.midbottom = (cx, cy - int(self.cell * 0.5))
            r.clamp_ip(self.map_rect)
            for dx in (0, w // 2 + 6, -(w // 2 + 6), w + 8, -(w + 8)):
                t = r.move(dx, 0).clamp(self.map_rect)
                if t.collidelist(placed) < 0:
                    r = t
                    break
            else:
                continue                   # no free spot: skip the less important bubble
            placed.append(r)
            fade = min(1.0, (until - now) / 0.5)
            surf = pygame.Surface((w, h + 6), pygame.SRCALPHA)
            fill = (*(TEXT if color in (TEXT, MUTED) else color), int(235 * fade))
            pygame.draw.rect(surf, fill, (0, 0, w, h), border_radius=h // 2)
            tx = max(8, min(w - 8, cx - r.x))
            pygame.draw.polygon(surf, fill, [(tx - 4, h - 1), (tx + 4, h - 1), (tx, h + 5)])
            if icon == "check":
                self.check(surf, 8, 4, ih - 4, (*INK, int(255 * fade)))
            img.set_alpha(int(255 * fade))
            surf.blit(img, (8 + iw, 3))
            self.screen.blit(surf, r.topleft)

    def draw_hover_tip(self):
        mpos = pygame.mouse.get_pos()
        if self.menu_open and self.menu_rect.collidepoint(mpos):
            return
        for b in self.buttons:
            if b.rect.collidepoint(mpos):
                if b.tip_text():
                    self.tooltip([b.tip_text()], mpos)
                return
        c = self.cell_at(mpos)
        a = self.robot_at(c, alive=False) if c else None
        if a is None or a.id == self.robot_sel:
            return
        cfg = self.sim.cfg
        cap = cfg.heavy_payload if a.kind == "heavy" else cfg.light_payload
        lines = [f"{R(a.id)} · {a.kind} robot (carries up to {cap} kg)"]
        if not a.alive:
            lines.append("Broken down" + ("" if a.id in self.sim.known_dead else ": not noticed yet"))
        else:
            lines.append(f"Battery {a.battery:.0f}%  ·  {DOING[a.mode]}")
            if a.carrying:
                o = a.carrying
                lines.append(f"Carrying parcel #{o.id} ({o.weight} kg) to {o.drop_name}")
        lines.append("Click for the robot card")
        self.tooltip(lines, mpos)

    def tooltip(self, lines, pos):
        f = self.f_body
        w = max(self.f_bold.size(t)[0] for t in lines) + 18
        h = len(lines) * f.get_linesize() + 10
        r = pygame.Rect(pos[0] + 16, pos[1] + 16, w, h)
        r.clamp_ip(self.screen.get_rect())
        pygame.draw.rect(self.screen, (34, 40, 56), r, border_radius=6)
        pygame.draw.rect(self.screen, FAINT, r, 1, border_radius=6)
        y = r.y + 5
        for i, t in enumerate(lines):
            self.text(t, self.f_bold if i == 0 else f, TEXT if i == 0 else MUTED, (r.x + 9, y))
            y += f.get_linesize()

    # ------------------------------------------------------------ chrome
    def draw_header(self):
        s = self.sim
        W = self.screen.get_width() - self.panel_w
        cy = self.header_h // 2
        r = self.text("Warehouse Robot Fleet", self.f_title, TEXT, (self.pad + 2, cy), "midleft")
        # Decentralized / Centralized switch: the active side carries the full status
        x = r.right + int(16 * self.s)
        h = self.f_bold.get_height() + int(12 * self.s)
        dot = int(4 * self.s)
        colors = (ACCENT, CENTRAL_C if s.central_up else BAD)
        for b, col in zip(self.modes, colors):
            w = self.f_bold.size(b.text())[0] + int(36 * self.s)
            b.rect = pygame.Rect(x, cy - h // 2, w, h)
            on = b.active()
            hover = b.rect.collidepoint(pygame.mouse.get_pos())
            fill = col if on else (BTN_HOVER if hover else BTN)
            fg = INK if on else MUTED
            pygame.draw.rect(self.screen, fill, b.rect, border_radius=h // 2)
            pygame.draw.circle(self.screen, INK if on else col,
                               (b.rect.x + int(14 * self.s), b.rect.centery), dot)
            self.text(b.text(), self.f_bold, fg, (b.rect.x + int(24 * self.s), b.rect.centery), "midleft")
            x = b.rect.right + int(6 * self.s)
        right = W - self.pad
        if self.paused or self.speed_i != 1:
            r = self.text("Paused" if self.paused else f"Speed {SPEEDS[self.speed_i][1]}", self.f_body,
                          PAD, (right, cy), "midright")
            right = r.left - 16
        loss = s.cfg.msg_loss
        r = self.text("Radio: clear" if not loss else f"Radio: {loss:.0%} lost", self.f_body,
                      PAD if loss else MUTED, (right, cy), "midright")
        self.text(f"Time {clock(s.tick)}", self.f_bold, TEXT, (r.left - 16, cy), "midright")

    def draw_caption(self):
        r = self.caption_rect
        rad = int(8 * self.s)
        pygame.draw.rect(self.screen, CARD, r, border_radius=rad)
        if self.break_mode:
            text, col = "Click the robot you want to break down (Esc to cancel).", BAD
        elif self.caption and time.time() < self.caption[3]:
            text, col = self.caption[0], self.caption[1]
        elif self.sim.cfg.mode == "decentralized":
            text, col = ("Each robot decides for itself which parcel to take. "
                         "There is no central computer."), MUTED
        elif self.sim.central_up:
            text, col = "One central computer tells every robot what to do.", MUTED
        else:
            text, col = "The central computer is down: no robot gets instructions.", BAD
        bar = pygame.Rect(r.x, r.y, int(7 * self.s), r.h)
        pygame.draw.rect(self.screen, col, bar, border_top_left_radius=rad, border_bottom_left_radius=rad)
        width = r.w - int(34 * self.s)
        font = self.f_cap if len(self.wrap(text, self.f_cap, width)) == 1 else self.f_bold
        lines = self.wrap(text, font, width)[:2]
        lh = font.get_linesize()
        y = r.centery - lh * len(lines) // 2
        for line in lines:
            self.text(line, font, col, (r.x + int(20 * self.s), y))
            y += lh

    def draw_buttons(self):
        self.text("WHAT IF…", self.f_small, FAINT, (self.bar_rect.x, self.what_if[0].rect.centery), "midleft")
        for b in self.what_if + self.right:
            self.draw_button(b)
        if self.menu_open:
            m = self.menu_rect
            pygame.draw.rect(self.screen, (30, 36, 50), m, border_radius=int(9 * self.s))
            pygame.draw.rect(self.screen, FAINT, m, 1, border_radius=int(9 * self.s))
            mpos = pygame.mouse.get_pos()
            for b in self.menu:
                hover = b.rect.collidepoint(mpos)
                self.draw_button(b, fill=BTN_HOVER if hover else (30, 36, 50), fg=TEXT, align="left")

    def draw_scene_card(self):
        if self.scene < 0:
            return
        title, sub = SCENES[self.scene]
        age = time.time() - self.scene_shown
        if age < 6:
            w = int(self.map_rect.w * 0.66)
            lines = self.wrap(sub, self.f_cap, w - 40)
            h = self.f_title.get_linesize() + len(lines) * self.f_cap.get_linesize() + 34
            r = pygame.Rect(0, 0, w, h)
            r.midtop = (self.map_rect.centerx, self.map_rect.y + int(self.map_rect.h * 0.1))
            alpha = int(240 * min(1.0, (6 - age) / 0.8))
            surf = pygame.Surface(r.size, pygame.SRCALPHA)
            pygame.draw.rect(surf, (20, 26, 38, alpha), surf.get_rect(), border_radius=12)
            pygame.draw.rect(surf, (*ACCENT, alpha), surf.get_rect(), 2, border_radius=12)
            self.screen.blit(surf, r.topleft)
            if alpha > 60:
                y = r.y + 14
                self.text(title, self.f_title, ACCENT, (r.centerx, y), "midtop")
                y += self.f_title.get_linesize() + 6
                for line in lines:
                    self.text(line, self.f_cap, TEXT, (r.centerx, y), "midtop")
                    y += self.f_cap.get_linesize()
        else:
            img = self.f_bold.render(title + "    (N: next scene)", True, INK)
            r = pygame.Rect(self.map_rect.x + 8, self.map_rect.y + 6, img.get_width() + 20, img.get_height() + 8)
            pygame.draw.rect(self.screen, ACCENT, r, border_radius=r.h // 2)
            self.screen.blit(img, img.get_rect(center=r.center))

    # ------------------------------------------------------------ side panel
    def draw_panel(self):
        s = self.sim
        P, pad = self.panel_rect, int(16 * self.s)
        pygame.draw.rect(self.screen, PANEL_BG, P)
        pygame.draw.line(self.screen, GRIDLINE, P.topleft, P.bottomleft, 2)
        x, y, w = P.x + pad, int(14 * self.s), P.w - 2 * pad
        self.text("Live results", self.f_head, TEXT, (x, y))
        y += self.f_head.get_linesize() + 6
        sm = s.summary(window=300)
        rescued = len(s.recovery_times)
        cards = [
            (str(sm["delivered"]), "parcels delivered", TEXT),
            (f"{sm['urgent_on_time_pct']:.0f}%" if s.urgent_times else "-",
             "urgent parcels on time", GOOD if sm["urgent_on_time_pct"] >= 85 else PAD),
            (f"{sm['alive']}/{sm['fleet']}", "robots working", GOOD if sm["alive"] == sm["fleet"] else PAD),
            (str(len(s.detect_latencies)),
             f"breakdowns handled, {rescued} parcel{'s' if rescued != 1 else ''} rescued", TEXT),
        ]
        gap = int(8 * self.s)
        cw, ch = (w - gap) // 2, int(70 * self.s)
        for i, (big, small, col) in enumerate(cards):
            r = pygame.Rect(x + (i % 2) * (cw + gap), y + (i // 2) * (ch + gap), cw, ch)
            pygame.draw.rect(self.screen, CARD, r, border_radius=int(8 * self.s))
            self.text(big, self.f_card, col, (r.x + 10, r.y + 3))
            ly = r.y + self.f_card.get_linesize()
            for line in self.wrap(small, self.f_small, cw - 18)[:2]:
                self.text(line, self.f_small, MUTED, (r.x + 10, ly))
                ly += self.f_small.get_linesize() - 2
        y += 2 * ch + gap + int(14 * self.s)
        legend_top = P.bottom - int(150 * self.s)
        self.link = None
        o = s.orders.get(self.selected)
        if self.advanced:
            self.draw_details(x, y, w, legend_top)
        elif self.robot_sel is not None and self.robot_sel < len(s.agents):
            self.draw_robot_card(s.agents[self.robot_sel], x, y, w, legend_top)
        elif o is not None:
            self.draw_inspector(o, x, y, w, legend_top)
        else:
            self.draw_feed(x, y, w, legend_top)
        self.draw_legend(x, legend_top, w)

    def draw_feed(self, x, y, w, bottom):
        self.text("What's happening", self.f_head, TEXT, (x, y))
        y += self.f_head.get_linesize() + 4
        hint_y = bottom - self.f_small.get_linesize() - 8
        if not self.feed:
            self.text("Events will appear here.", self.f_body, MUTED, (x, y))
        indent = int(46 * self.s)
        for t, text, col in self.feed:
            lines = self.wrap(text, self.f_body, w - indent)
            if y + len(lines) * self.f_body.get_linesize() > hint_y:
                break
            self.text(clock(t), self.f_small, FAINT, (x, y + 2))
            for line in lines:
                self.text(line, self.f_body, MUTED if col == TEXT else col, (x + indent, y))
                y += self.f_body.get_linesize()
            y += 4
        self.text("Click a robot for its card, or a parcel for its story.", self.f_small, ACCENT,
                  (x, hint_y))

    def kv(self, x, y, w, key, val, col=TEXT):
        self.text(key, self.f_body, MUTED, (x, y))
        self.text(val, self.f_bold, col, (x + int(w * 0.42), y))
        return y + self.f_body.get_linesize()

    def draw_robot_card(self, a, x, y, w, bottom):
        s, cfg = self.sim, self.sim.cfg
        lh = self.f_body.get_linesize()
        cap = cfg.heavy_payload if a.kind == "heavy" else cfg.light_payload
        self.text(f"Robot {R(a.id)}", self.f_head, TEXT, (x, y))
        self.text(f"{a.kind.capitalize()} · max {cap} kg", self.f_bold, MUTED, (x + w, y + 2), "topright")
        y += self.f_head.get_linesize() + 6
        hint_y = bottom - self.f_small.get_linesize() - 8
        if not a.alive:
            y = self.kv(x, y, w, "Status", "Broken down", BAD)
            noticed = a.id in s.known_dead
            y = self.kv(x, y, w, "Noticed by others", "yes" if noticed else "not yet",
                        GOOD if noticed else PAD)
            if noticed:
                self.text("Its jobs were handed to other robots.", self.f_body, MUTED, (x, y + 4))
            self.text("Click empty floor or press Esc to close.", self.f_small, ACCENT, (x, hint_y))
            return
        y = self.kv(x, y, w, "Status", DOING[a.mode], MODE_COLORS[a.mode])
        bcol = GOOD if a.battery > 50 else PAD if a.battery > 25 else BAD
        y = self.kv(x, y, w, "Battery", f"{a.battery:.0f}%", bcol)
        place = s.wms.place(a.pos)
        y = self.kv(x, y, w, "Location", place if not place.startswith("Floor") else "Aisle")
        job = a.tasks[0] if a.tasks else None
        if job:
            y = self.kv(x, y, w, "Current order", f"#{job.id} · {job.weight} kg"
                        + (" · URGENT" if job.urgent else ""), URGENT_C if job.urgent else TEXT)
            if a.carrying:
                d = s.wh.dist(a.pos, job.drop)
                y = self.kv(x, y, w, "Distance to drop", f"{d} cells ({job.drop_name})")
            else:
                d = s.wh.dist(a.pos, job.pickup)
                y = self.kv(x, y, w, "Distance to pickup", f"{d} cells ({renumber(job.pickup_name)})")
            won = next((c for aid, c, _ in job.last_auction if aid == a.id and c < INF), None)
            if won is not None:
                y = self.kv(x, y, w, "Won it with offer", f"{won:.1f}")
            queued = len(a.tasks) - 1
            if queued:
                y = self.kv(x, y, w, "Queued next", ", ".join(f"#{o.id}" for o in a.tasks[1:]))
        else:
            y = self.kv(x, y, w, "Current order", "none")
        if s.cfg.mode == "decentralized":
            near = ", ".join(R(b.id) for b in sorted(a.neighbors, key=lambda b: b.id)) or "none"
            lines = self.wrap(near, self.f_bold, int(w * 0.56))
            self.text("Neighbours (radio)", self.f_body, MUTED, (x, y))
            for line in lines[:2]:
                self.text(line, self.f_bold, TEXT, (x + int(w * 0.42), y))
                y += lh
            # live decisions on open orders this robot knows about
            known = [s.orders[oid] for oid in a.known_orders
                     if oid in s.orders and s.orders[oid].status == "open"]
            known.sort(key=lambda o: (not o.urgent, o.created))
            y += 8
            self.text("Its decisions right now", self.f_bold, MUTED, (x, y))
            y += lh
            if not known:
                self.text("No open orders in radio range.", self.f_body, FAINT, (x, y))
                y += lh
            for o in known[:4]:
                c, why = s.bid_explain(a, o)
                tag = f"#{o.id} ({o.weight} kg{', urgent' if o.urgent else ''})"
                if c < INF:
                    self.text(f"{tag}  →  offer {c:.1f}", self.f_body, GOOD, (x, y))
                else:
                    self.text(f"{tag}  →  declined: {short_reason(why) or why}", self.f_body, FAINT, (x, y))
                y += lh
            self.text("Lower offer = closer, less busy, more battery to spare.", self.f_small, FAINT, (x, y + 2))
            y += lh
        else:
            y += 6
            self.text("In this mode the central computer decides for it.", self.f_body, CENTRAL_C, (x, y))
            y += lh
        if job:
            y += 8
            img = self.f_bold.render(f"Show parcel #{job.id}'s story", True, ACCENT)
            r = img.get_rect(topleft=(x, y))
            self.screen.blit(img, r)
            pygame.draw.line(self.screen, ACCENT, r.bottomleft, r.bottomright, 1)
            self.link = (r.inflate(6, 6), job.id)
        self.text("Click empty floor or press Esc to close.", self.f_small, ACCENT, (x, hint_y))

    def draw_inspector(self, o, x, y, w, bottom):
        s = self.sim
        lh = self.f_body.get_linesize()
        self.text(f"Parcel #{o.id}", self.f_head, TEXT, (x, y))
        self.text(f"{o.weight} kg · {'URGENT' if o.urgent else 'normal'}", self.f_bold,
                  URGENT_C if o.urgent else MUTED, (x + w, y + 2), "topright")
        y += self.f_head.get_linesize() + 2
        self.text(f"{renumber(o.pickup_name)}  →  {o.drop_name}", self.f_body, TEXT, (x, y))
        y += lh
        if o.status == "open":
            st, col = "Waiting for a robot", PAD
        elif o.status == "assigned":
            st, col = (f"{R(o.owner)} is delivering it" if o.picked_at else
                       f"{R(o.owner)} is on the way to pick it up"), ACCENT
        else:
            st, col = "Delivered", GOOD
        if o.deadline is not None and o.status != "delivered":
            left = o.deadline - s.tick
            st += f" · {left} sec left" if left >= 0 else f" · {-left} sec late"
        self.text(st, self.f_bold, col, (x, y))
        y += self.f_bold.get_linesize() + 8
        hint_y = bottom - self.f_small.get_linesize() - 8
        offers_h = (lh * (1 + min(4, len(o.last_auction))) + 12) if o.last_auction else 0
        self.text("Its story", self.f_bold, MUTED, (x, y))
        y += self.f_bold.get_linesize()
        indent = int(46 * self.s)
        rows = []
        for t, msg in o.history:
            text = plain(msg)
            col = BAD if any(k in text for k in ("broke down", "lost", "late", "returned the job")) \
                else GOOD if text.startswith("Delivered") else TEXT
            for i, line in enumerate(self.wrap(text, self.f_body, w - indent)):
                rows.append((clock(t) if i == 0 else "", line, col))
        room = max(2, (hint_y - offers_h - y) // lh)
        if len(rows) > room:
            rows = rows[-(room - 1):]
            self.text("… earlier steps hidden", self.f_small, FAINT, (x, y))
            y += lh
        for t, line, col in rows:
            self.text(t, self.f_small, FAINT, (x, y + 2))
            self.text(line, self.f_body, col, (x + indent, y))
            y += lh
        if o.last_auction:
            y += 12
            who = "Who offered to take it (lowest offer wins)" if s.cfg.mode == "decentralized" \
                else "Central computer compared every robot"
            self.text(who, self.f_bold, MUTED, (x, y))
            y += lh
            for aid, c, why in o.last_auction[:4]:
                kind = s.agents[aid].kind
                if c < INF:
                    r = self.text(f"{R(aid)} ({kind})  offer {c:.1f}", self.f_body,
                                  GOOD if aid == o.owner else TEXT, (x, y))
                    if aid == o.owner:
                        self.check(self.screen, r.right + 10, y + 3, lh - 8, GOOD)
                        self.text("chosen", self.f_body, GOOD, (r.right + 10 + lh, y))
                else:
                    self.text(f"{R(aid)} ({kind})  declined: {short_reason(why) or why}",
                              self.f_body, FAINT, (x, y))
                y += lh
        self.text("Click empty floor or press Esc to close.", self.f_small, ACCENT, (x, hint_y))

    def draw_details(self, x, y, w, bottom):
        s = self.sim
        sm = s.summary(window=300)
        self.text("Technical details", self.f_head, TEXT, (x, y))
        y += self.f_head.get_linesize() + 4
        rows = [
            ("Mode", s.cfg.mode),
            ("Tick (1 tick = 1 sec)", str(s.tick)),
            ("Throughput /100 ticks", f"{sm['throughput_per_100']:.1f}"),
            ("Avg delivery time", f"{sm['avg_delivery_time']:.1f}"),
            ("  urgent / normal", f"{sm['urgent_delivery_time']:.1f} / {sm['normal_delivery_time']:.1f}"),
            ("Open orders", str(sm["open_orders"])),
            ("Failure detection", f"{sm['detect_latency']:.1f} ticks"),
            ("Order recovery", f"{sm['recovery_time']:.1f} ticks"),
            ("Orders re-auctioned", str(sm["reassigned"])),
            ("Handed back (energy)", str(sm["released"])),
            ("Busiest node msgs/tick", f"{sm['max_node_load']:.1f}"),
            ("Msgs / robot / tick", f"{sm['msgs_per_agent_tick']:.2f}"),
            ("Radio msgs lost", str(sm["msgs_lost"])),
            ("Heartbeat timeouts", str(sm["suspicions"])),
            ("Energy / delivery", f"{sm['energy_per_delivery']:.1f} %"),
            ("Battery failures", str(sm["battery_failures"])),
        ]
        lh = self.f_mono.get_linesize() + 2
        for label, val in rows:
            if y + lh > bottom:
                break
            self.text(label, self.f_mono, MUTED, (x, y))
            self.text(val, self.f_mono, TEXT, (x + w, y), "topright")
            y += lh

    def draw_legend(self, x, y, w):
        C = int(18 * self.s)
        pygame.draw.line(self.screen, GRIDLINE, (x, y - 6), (x + w, y - 6), 1)
        self.text("Legend", self.f_bold, MUTED, (x, y))
        y += self.f_bold.get_linesize() + 2
        items = [("light", "Light robot (10 kg)"), ("heavy", "Heavy robot (25 kg)"),
                 ("parcel", "Parcel"), ("urgent", "Urgent parcel"),
                 ("drop", "Delivery spot"), ("charger", "Charger"),
                 ("broken", "Broken robot"), ("dock", "Dock (D1-D5)")]
        col_w, row_h = w // 2, int(C * 1.25)
        for i, (kind, label) in enumerate(items):
            ix = x + (i % 2) * col_w + C // 2
            iy = y + (i // 2) * row_h + C // 2
            if kind == "light":
                pygame.draw.circle(self.screen, MODE_COLORS["pickup"], (ix, iy), int(C * 0.4))
            elif kind in ("heavy", "dock"):
                r = pygame.Rect(0, 0, int(C * 0.9), int(C * 0.9))
                r.center = (ix, iy)
                pygame.draw.rect(self.screen, MODE_COLORS["deliver"] if kind == "heavy" else DOCK, r,
                                 border_radius=4)
            elif kind in ("parcel", "urgent"):
                self.parcel_icon((ix, iy), int(C * 0.7), kind == "urgent")
            elif kind == "drop":
                k = int(C * 0.35)
                pygame.draw.polygon(self.screen, ACCENT, [(ix, iy - k), (ix + k, iy), (ix, iy + k), (ix - k, iy)], 2)
            elif kind == "charger":
                self.bolt((ix, iy), C * 0.45, PAD)
            elif kind == "broken":
                k = int(C * 0.3)
                pygame.draw.line(self.screen, BAD, (ix - k, iy - k), (ix + k, iy + k), 3)
                pygame.draw.line(self.screen, BAD, (ix - k, iy + k), (ix + k, iy - k), 3)
            self.text(label, self.f_small, MUTED, (ix + C // 2 + 6, iy), "midleft")
        y += 4 * row_h + 4
        lx = x
        for m, label in (("idle", "waiting"), ("pickup", "to pickup"), ("deliver", "delivering"),
                         ("charge", "charging")):
            pygame.draw.circle(self.screen, MODE_COLORS[m], (lx + 5, y + 8), 5)
            r = self.text(label, self.f_small, MUTED, (lx + 13, y))
            lx = r.right + 10

    # ------------------------------------------------------------ loop
    def draw(self):
        self.screen.fill(BG)
        self.draw_header()
        self.draw_map()
        if self.show_links:
            self.draw_links()
        self.draw_orders()
        self.draw_parcels()
        self.draw_agents()
        self.draw_bubbles()
        self.draw_scene_card()
        self.draw_caption()
        self.draw_panel()
        self.draw_buttons()
        self.draw_hover_tip()

    def run(self):
        running = True
        while running:
            dt = self.clock.tick(60) / 1000
            self.frame += 1
            for e in pygame.event.get():
                if not self.handle(e):
                    running = False
            pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_CROSSHAIR if self.break_mode
                                    else pygame.SYSTEM_CURSOR_ARROW)
            if not self.paused:
                self.acc += dt * SPEEDS[self.speed_i][0]
                n = 0
                while self.acc >= 1 and n < 20:
                    self.sim.step()
                    self.watch()
                    self.acc -= 1
                    n += 1
            self.draw()
            pygame.display.flip()
        pygame.quit()


if __name__ == "__main__":
    Demo("centralized" if "--central" in sys.argv else "decentralized",
         fullscreen="--window" not in sys.argv).run()
