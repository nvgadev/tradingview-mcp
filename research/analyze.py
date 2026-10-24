#!/usr/bin/env python3
"""Offline study of the GOPI entries (research/trades.txt, from the research copy's labels).
All R figures are in units of the live indicator's own risk (1R = 1.5 ATR at entry)."""
import statistics as st
import sys
from collections import defaultdict

PV = 10.0        # $ per point, MGC
COST = 5.0       # $ round trip
ACCOUNT = 10000.0

def load(path="research/trades.txt"):
    T = []
    for ln in open(path):
        p = ln.strip().split("|")
        if len(p) < 18: continue
        kind, rs = (p[15].split(":") + [""])[:2]
        T.append(dict(tid=int(p[1]), dir=int(p[2]), entry=float(p[3]), rdist=float(p[4]),
                      hr=int(p[5]), mn=int(p[6]), dow=int(p[7]), atrp=float(p[8]), score=int(p[9]),
                      reidx=int(p[10]), h60=int(p[11]), hD=int(p[12]), vw=int(p[13]), bsr=int(p[14]),
                      kind=kind, live_r=float(rs) if rs not in ("", None) else None,
                      fav=[float(x) for x in p[16].split(",")], adv=[float(x) for x in p[17].split(",")]))
    T.sort(key=lambda t: t["tid"])
    return T

def cost_r(t): return COST / (t["rdist"] * PV)

def sim_live(t):
    """The live staged exit: stop -1R; +1R -> stop +0.2R; +2R -> stop +1R; +3R exit."""
    stop, stage = -1.0, 0
    for f, a in zip(t["fav"], t["adv"]):
        if a >= -stop:                     # stop is checked first (as in the Pine code)
            return stop
        if stage < 1 and f >= 1: stage, stop = 1, 0.2
        if stage < 2 and f >= 2: stage, stop = 2, 1.0
        if f >= 3: return 3.0
    return None                            # unresolved inside the window

def sim_fixed(t, s, tp, be_at=None, be_lock=0.0):
    """Stop s R, one target tp R, optional break-even move. Adverse-first within a bar."""
    stop = -s; moved = False
    for f, a in zip(t["fav"], t["adv"]):
        if a >= -stop: return stop
        if be_at is not None and not moved and f >= be_at: stop, moved = be_lock, True
        if f >= tp: return tp
    return None

def sim_trail(t, s, act, gap):
    """Stop s R; once +act R is reached trail the stop `gap` R behind the best excursion."""
    stop = -s; best = 0.0
    for f, a in zip(t["fav"], t["adv"]):
        if a >= -stop: return stop
        best = max(best, f)
        if best >= act: stop = max(stop, best - gap)
    return None

def summarize(T, fn, label, show=True):
    rs, unres = [], 0
    for t in T:
        r = fn(t)
        if r is None: unres += 1; r = 0.0
        rs.append(r - cost_r(t))
    n = len(rs); mean = st.mean(rs); sd = st.pstdev(rs) or 1e-9
    wins = [r for r in rs if r > 0]; loss = [r for r in rs if r <= 0]
    eq = peak = dd = 0.0
    for r in rs:
        eq += r; peak = max(peak, eq); dd = max(dd, peak - eq)
    out = dict(label=label, n=n, mean=mean, win=len(wins) / n, avgw=st.mean(wins) if wins else 0,
               avgl=st.mean(loss) if loss else 0, tot=sum(rs), dd=dd, unres=unres, se=sd / n ** .5)
    if show:
        print(f"{label:34s} n={n:3d} exp={mean:+.3f}R (±{1.96*out['se']:.2f}) win={out['win']*100:4.1f}% "
              f"avgW={out['avgw']:+.2f} avgL={out['avgl']:+.2f} total={out['tot']:+6.1f}R maxDD={dd:5.1f}R unresolved={unres}")
    return out

if __name__ == "__main__":
    T = load()
    print(f"{len(T)} trades; tid range {T[0]['tid']}..{T[-1]['tid']}")
    # --- 1. does the simulator reproduce the live results? ---
    both = [(t["live_r"], sim_live(t)) for t in T if t["live_r"] is not None and t["kind"] != "SESSION" and sim_live(t) is not None]
    same = sum(1 for a, b in both if abs(a - b) < 0.11)
    print(f"simulator vs live result: {same}/{len(both)} match within 0.1R")
    print()
    # --- 2. edge in the ENTRY itself (independent of the exit rule) ---
    def p_first(t, lvl):
        for f, a in zip(t["fav"], t["adv"]):
            if a >= lvl and f >= lvl: return "tie"
            if a >= lvl: return "loss"
            if f >= lvl: return "win"
        return "none"
    for lvl in (0.5, 1.0, 1.5, 2.0):
        c = defaultdict(int)
        for t in T: c[p_first(t, lvl)] += 1
        dec = c["win"] + c["loss"] + c["tie"]
        print(f"reach +{lvl}R before -{lvl}R: win {c['win']}  loss {c['loss']}  same-bar {c['tie']}  none {c['none']}"
              f"   -> {c['win']/dec*100:.1f}% of decided (50% = coin flip; after costs you need ~{50+ (COST/(st.mean(t['rdist'] for t in T)*PV))/lvl*50:.0f}%)")
    print()
    summarize(T, sim_live, "live staged exit (simulated)")
    for s in (0.75, 1.0, 1.5, 2.0):
        for tp in (1.0, 1.5, 2.0, 3.0, 4.0):
            summarize(T, lambda t, s=s, tp=tp: sim_fixed(t, s, tp), f"stop {s}R / target {tp}R")
    print()
    for s in (1.0, 1.5, 2.0):
        for act, gap in ((1.0, 0.5), (1.5, 1.0), (2.0, 1.0), (3.0, 1.5)):
            summarize(T, lambda t, s=s, a=act, g=gap: sim_trail(t, s, a, g), f"stop {s}R trail after {act}R gap {gap}R")
