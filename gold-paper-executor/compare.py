#!/usr/bin/env python3
"""Compare trading-day policies on the paper trades the system actually took.

    python3 compare.py            # reads trades.csv next to this file

The system takes EVERY signal (one trade at a time).  A day rule such as "only the first
trade of the day" never changes the trades before it, so each policy can be computed exactly
from the same log - no second account needed.

"Close in 3 parts" is a simulation from the same trades: 1/3 of the size leaves at TP1 (+1R),
1/3 at TP2 (+2R) and the last 1/3 at the final exit, using the stage each trade reached.
It assumes the part orders fill at the target price (the same assumption the final target uses).
"""
import csv
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.realpath(__file__))


def scaled_net(row):
    """Net $ if the position had been closed in three equal parts (needs stage_reached)."""
    risk = float(row["risk_usd"])
    r_final = float(row["r_multiple"])
    stage = int(float(row.get("stage_reached") or 0))
    leg1 = 1.0 if stage >= 1 else r_final
    leg2 = 2.0 if stage >= 2 else r_final
    gross = risk * (leg1 + leg2 + r_final) / 3.0
    return gross - float(row["cost_usd"])


def stats(pnls):
    n = len(pnls)
    if n == 0:
        return None
    eq = peak = dd = 0.0
    streak = worst = 0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        streak = streak + 1 if p <= 0 else 0
        worst = max(worst, streak)
    return dict(n=n, total=sum(pnls), avg=sum(pnls) / n, win=sum(1 for p in pnls if p > 0) / n * 100,
                dd=dd, streak=worst)


def stop_after(day, want_loss):
    out = []
    for p in day:
        out.append(p)
        if (p <= 0) == want_loss:
            break
    return out


def build(rows):
    rows = sorted(rows, key=lambda r: r["opened_et"])
    have_stage = all(r.get("stage_reached") not in (None, "") for r in rows)
    days_all, days_sc = defaultdict(list), defaultdict(list)
    for r in rows:
        days_all[r["opened_et"][:10]].append(float(r["net_usd"]))
        if have_stage:
            days_sc[r["opened_et"][:10]].append(scaled_net(r))

    def run(days, pick):
        seq = []
        for d in sorted(days):
            seq += pick(days[d])
        return stats(seq)

    out = [
        ("Every trade, all-in / all-out (what the system does)", run(days_all, lambda d: d)),
        ("Only the 1st trade of each day", run(days_all, lambda d: d[:1])),
        ("Only the first 2 trades of each day", run(days_all, lambda d: d[:2])),
        ("Stop for the day after the 1st loss", run(days_all, lambda d: stop_after(d, True))),
        ("Stop for the day after the 1st win", run(days_all, lambda d: stop_after(d, False))),
    ]
    if have_stage:
        out += [("Every trade, closed in 3 parts (1/3 per target)", run(days_sc, lambda d: d)),
                ("1st trade of the day, closed in 3 parts", run(days_sc, lambda d: d[:1]))]
    return out, sorted(days_all), have_stage


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "trades.csv")
    if not os.path.exists(path):
        sys.exit("no trades yet (trades.csv does not exist)")
    rows = list(csv.DictReader(open(path)))
    results, days, have_stage = build(rows)
    print(f"{len(rows)} paper trades on {len(days)} days   ({days[0]} -> {days[-1]})\n")
    print(f"{'Rule':55s} {'trades':>6s} {'total $':>9s} {'avg $':>7s} {'wins %':>7s} {'biggest drop':>13s} {'worst streak':>13s}")
    for name, s in results:
        if s:
            print(f"{name:55s} {s['n']:6d} {s['total']:+9.0f} {s['avg']:+7.1f} {s['win']:7.0f} {s['dd']:13.0f} {s['streak']:13d}")
    if not have_stage:
        print("\n(the 3-parts rows need the stage_reached column, which only newer trades have)")
    if len(rows) < 100:
        print(f"\nOnly {len(rows)} trades so far - below ~100 the differences are mostly luck. Keep collecting.")


if __name__ == "__main__":
    main()
