"""
Benchmark: decentralized swarm vs centralized fleet manager.

Produces graphs for the PPT in results/:
  1. scalability.png  - throughput, delivery time and busiest-node load
                        as the fleet grows
  2. resilience.png   - deliveries over time while robots fail and the
                        central server crashes
  3. radio_loss.png   - throughput, delivery time and failure detection
                        as more and more radio messages are lost
and prints a summary table.

Run:  python benchmark.py          (about 1-2 minutes)
      python benchmark.py --quick  (fewer seeds, for a fast check)
"""

import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from swarm.core import Config, Simulation

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
QUICK = "--quick" in sys.argv
SEEDS = [1, 2] if QUICK else [1, 2, 3, 4, 5]
FLEETS = [6, 9, 12, 16, 20, 24]
TICKS = 800 if QUICK else 1500
MODES = ("decentralized", "centralized")
COLORS = {"decentralized": "#1f77b4", "centralized": "#d62728"}
LABELS = {"decentralized": "Decentralized swarm (ours)",
          "centralized": "Centralized fleet manager"}


def run(mode, n, seed, ticks, events=None, **kw):
    # demand grows with the fleet so bigger fleets have work to do
    sim = Simulation(Config(n_agents=n, mode=mode, seed=seed, order_rate=0.01 * n, **kw))
    events = events or {}
    for _ in range(ticks):
        if sim.tick in events:
            events[sim.tick](sim)
        sim.step()
    return sim


def scalability():
    res = {m: {"thr": [], "thr_sd": [], "dt": [], "dt_sd": [], "load": [], "load_sd": []}
           for m in MODES}
    for m in MODES:
        for n in FLEETS:
            thr, dt, load = [], [], []
            for s in SEEDS:
                sm = run(m, n, s, TICKS).summary()
                thr.append(sm["throughput_per_100"])
                dt.append(sm["avg_delivery_time"])
                load.append(sm["max_node_load"])
            for key, xs in (("thr", thr), ("dt", dt), ("load", load)):
                res[m][key].append(np.mean(xs))
                res[m][key + "_sd"].append(np.std(xs))
            print(f"  {m:13s} n={n:2d}  throughput={np.mean(thr):5.1f}/100t  "
                  f"delivery={np.mean(dt):5.1f}t  busiest node={np.mean(load):5.1f} msgs/t")

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))
    panels = (("thr", "Deliveries per 100 ticks", "Throughput (higher is better)"),
              ("dt", "Ticks from order to delivery", "Avg delivery time (lower is better)"),
              ("load", "Messages handled per tick", "Load on the busiest node"))
    for ax, (key, ylabel, title) in zip(axes, panels):
        for m in MODES:
            ax.errorbar(FLEETS, res[m][key], yerr=res[m][key + "_sd"], marker="o",
                        capsize=3, color=COLORS[m], label=LABELS[m])
        ax.set_xlabel("Fleet size (robots)")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(alpha=0.3)
    axes[0].legend(loc="upper left")
    fig.suptitle("Scalability: decentralized swarm vs centralized fleet manager "
                 f"(mean of {len(SEEDS)} runs, {TICKS} ticks each)")
    fig.tight_layout()
    path = os.path.join(OUT, "scalability.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return res, path


def resilience():
    n, ticks = 16, 2000
    kill_ticks, crash_tick = (500, 650, 800), 1200

    def kill_busiest(sim):
        busy = [a for a in sim.alive_agents if a.carrying] or sim.alive_agents
        sim.kill(busy[0].id)

    events = {t: kill_busiest for t in kill_ticks}
    events[crash_tick] = lambda sim: sim.crash_server()

    series, stats = {}, {}
    for m in MODES:
        runs = [run(m, n, s, ticks, dict(events)) for s in SEEDS]
        series[m] = np.mean([r.delivered_series for r in runs], axis=0)
        stats[m] = {
            "before_crash": np.mean([r.delivered_series[crash_tick] for r in runs]),
            "after_crash": np.mean([r.delivered_series[-1] - r.delivered_series[crash_tick]
                                    for r in runs]),
            "detect": np.mean([np.mean(r.detect_latencies) for r in runs if r.detect_latencies]),
            "reassigned": np.mean([r.reassigned for r in runs]),
        }

    fig, ax = plt.subplots(figsize=(11, 5))
    for m in MODES:
        ax.plot(series[m], color=COLORS[m], lw=2.2, label=LABELS[m])
    for i, t in enumerate(kill_ticks):
        ax.axvline(t, color="gray", ls=":", lw=1.2,
                   label="Robot failure" if i == 0 else None)
    ax.axvline(crash_tick, color="black", ls="--", lw=1.5, label="Central server crash")
    ax.annotate("Centralized fleet stops:\nno new deliveries",
                xy=(crash_tick + 150, series["centralized"][crash_tick + 150]),
                xytext=(crash_tick + 250, series["centralized"][crash_tick] * 0.55),
                arrowprops=dict(arrowstyle="->", color=COLORS["centralized"]),
                color=COLORS["centralized"])
    ax.annotate("Swarm keeps delivering",
                xy=(ticks - 300, series["decentralized"][ticks - 300]),
                xytext=(ticks - 750, series["decentralized"][-1] * 1.02),
                arrowprops=dict(arrowstyle="->", color=COLORS["decentralized"]),
                color=COLORS["decentralized"])
    ax.set_xlabel("Time (ticks)")
    ax.set_ylabel("Packages delivered (cumulative)")
    ax.set_title(f"Resilience: {n} robots, 3 robot failures, then server crash "
                 f"(mean of {len(SEEDS)} runs)")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left")
    fig.tight_layout()
    path = os.path.join(OUT, "resilience.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return stats, path


def radio_loss():
    """Robots fail during the run; how much radio loss can the swarm take?"""
    n, ticks = 16, TICKS
    losses = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
    # 3 failures per run, each a robot carrying a package; 10 runs per point
    seeds = list(range(1, 11)) if not QUICK else SEEDS

    def kill_carrier(sim):
        busy = [a for a in sim.alive_agents if a.carrying] or sim.alive_agents
        sim.kill(busy[0].id)

    kills = {t: kill_carrier for t in (300, 600, 900)}
    keys = ("throughput_per_100", "avg_delivery_time", "detect_latency",
            "urgent_on_time_pct", "battery_failures")
    res = {m: {k: [] for k in keys} for m in MODES}
    stale = {m: [] for m in MODES}
    for m in MODES:
        for loss in losses:
            sims = [run(m, n, s, ticks, dict(kills), msg_loss=loss) for s in seeds]
            sms = [x.summary() for x in sims]
            for k in keys:
                res[m][k].append(np.mean([sm[k] for sm in sms]))
            # orders older than 200 ticks still not delivered = stuck
            stale[m].append(np.mean([sum(1 for o in x.orders.values() if o.status != "delivered"
                                         and x.tick - o.created > 200) for x in sims]))
            print(f"  {m:13s} loss={loss:.0%}  throughput={res[m]['throughput_per_100'][-1]:5.1f}  "
                  f"delivery={res[m]['avg_delivery_time'][-1]:5.1f}t  "
                  f"detect={res[m]['detect_latency'][-1]:4.1f}t  "
                  f"urgent on time={res[m]['urgent_on_time_pct'][-1]:4.0f}%  stuck={stale[m][-1]:.1f}")

    pct = [100 * x for x in losses]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))
    panels = (("throughput_per_100", "Deliveries per 100 ticks", "Throughput"),
              ("avg_delivery_time", "Ticks from order to delivery", "Avg delivery time"),
              ("detect_latency", "Ticks until a failure is detected", "Robot-failure detection"))
    for ax, (key, ylabel, title) in zip(axes, panels):
        for m in MODES:
            ax.plot(pct, res[m][key], marker="o", color=COLORS[m], label=LABELS[m])
        ax.set_xlabel("Radio messages lost (%)")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(alpha=0.3)
    axes[0].set_ylim(bottom=0)
    axes[0].legend(loc="lower left")
    fig.suptitle(f"Robustness to radio message loss: {n} robots, 3 robot failures per run "
                 f"(mean of {len(seeds)} runs)")
    fig.tight_layout()
    path = os.path.join(OUT, "radio_loss.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return res, stale, path


def main():
    os.makedirs(OUT, exist_ok=True)
    print("1/3 Scalability sweep")
    res, p1 = scalability()
    print("2/3 Resilience timeline")
    stats, p2 = resilience()
    print("3/3 Radio message loss")
    lres, lstale, p3 = radio_loss()

    print("\nSummary")
    i = FLEETS.index(20)
    for m in MODES:
        st = stats[m]
        print(f"  {LABELS[m]}")
        print(f"    20 robots: throughput {res[m]['thr'][i]:.1f}/100 ticks, "
              f"delivery {res[m]['dt'][i]:.1f} ticks, busiest node {res[m]['load'][i]:.1f} msgs/tick")
        print(f"    failures: detected in {st['detect']:.1f} ticks on avg, "
              f"{st['reassigned']:.1f} orders re-auctioned per run")
        print(f"    deliveries after server crash: {st['after_crash']:.0f}")
        print(f"    at 30% radio loss: throughput {lres[m]['throughput_per_100'][3]:.1f}/100 ticks, "
              f"delivery {lres[m]['avg_delivery_time'][3]:.1f} ticks, "
              f"stuck orders {lstale[m][3]:.1f}")
    print(f"\nSaved {p1}\nSaved {p2}\nSaved {p3}")


if __name__ == "__main__":
    main()
