import sys, math, statistics as st
sys.path.insert(0, "research")
from analyze import load, cost_r, sim_fixed, sim_trail
T = load()

def first(t, lvl=1.0):
    for f, a in zip(t["fav"], t["adv"]):
        if a >= lvl: return 0
        if f >= lvl: return 1
    return None

def grp(name, key):
    g = {}
    for t in T:
        g.setdefault(key(t), []).append(t)
    print(f"\n{name}")
    for k in sorted(g, key=str):
        ts = g[k]
        w = [first(t) for t in ts if first(t) is not None]
        n = len(w); p = sum(w) / n if n else float("nan")
        ci = 1.96 * math.sqrt(p * (1 - p) / n) if n else 0
        ex = st.mean([ (sim_trail(t, 2.0, 1.0, 0.5) or 0) - cost_r(t) for t in ts])
        print(f"  {str(k):14s} n={len(ts):3d}  P(+1R before -1R)={p*100:5.1f}% (±{ci*100:4.1f})   exp[stop2R trail]={ex:+.3f}R")

grp("hour of day (Chicago time)", lambda t: f"{t['hr']:02d}h")
grp("weekday (1=Sun..7=Sat)", lambda t: t["dow"])
grp("direction", lambda t: "LONG" if t["dir"] == 1 else "SHORT")
grp("1h trend agrees with trade", lambda t: t["h60"])
grp("daily trend agrees with trade", lambda t: t["hD"])
grp("price on trade side of VWAP", lambda t: t["vw"])
grp("entry number (1=first after flip)", lambda t: min(t["reidx"], 3))
grp("confidence score", lambda t: "55-64" if t["score"] < 65 else "65-74" if t["score"] < 75 else "75+")
q = sorted(t["atrp"] for t in T); a, b = q[len(q)//3], q[2*len(q)//3]
grp("volatility (ATR % of price) tertile", lambda t: "low" if t["atrp"] < a else "mid" if t["atrp"] < b else "high")
half = len(T) // 2
grp("first half vs second half of the sample", lambda t: "1st" if T.index(t) < half else "2nd")
