"""GC paper ledger - pure logic.  Turns GOPI HEDGE X PRO webhook events into paper trades.

No network and no clock reads in here: the receiver passes `now` (epoch seconds), so every
rule is testable.  Prices are points, money is USD.  The ledger never decides to trade - it
only mirrors what the indicator says (ENTRY / TP1 / TP2 / EXIT) and applies the risk rails.
"""
import csv
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import messages as M

ET = ZoneInfo("America/New_York")
STOP_KINDS = {"SL", "BE", "TP1LOCK"}      # resting stop orders  -> adverse slippage
LIMIT_KINDS = {"TP3"}                      # resting limit order  -> filled at the level
# anything else (FLIP, SESSION, MANUAL)    -> market order, adverse slippage

DEFAULTS = {
    "enabled": True,
    "account_start_usd": 10000.0,
    "qty": 1,
    "tick_size": 0.25,
    "slippage_ticks": 1,
    "commission_per_side_usd": 0.6,       # per contract
    # False = measure the signals even though a $5k account could not really hold the margin.
    # True  = behave like a real broker: refuse a trade the balance cannot margin.
    "enforce_margin": False,
    # Webull sandbox preview 2026-10-13: 1 GC = $22,976, 1 MGC = $2,298 (about 5.5% of notional).
    # Webull shows no cheaper day margin, so intraday = initial here.
    "margin": {
        "GC":  {"intraday_usd": 22976.0, "overnight_pct_of_notional": 0.055},
        "MGC": {"intraday_usd": 2298.0,  "overnight_pct_of_notional": 0.055},
        "1OZ": {"intraday_usd": 226.6,   "overnight_pct_of_notional": 0.055},
    },
    # The TradingView chart stays on GC1! (signals unchanged); the account trades the micro.
    # symbol = the actual Webull contract; roll it by hand every two months (GCZ6 -> GCG7 ...).
    "instrument_map": {
        "GC":  {"trade_root": "1OZ", "pv": 1.0, "qty": 6, "tick": 0.25,
                "contracts": [{"symbol": "1OZZ6", "until": "2026-11-20"},
                              {"symbol": "1OZG7", "until": "2027-01-22"}]},
        "MGC": {"trade_root": "1OZ", "pv": 1.0, "qty": 6, "tick": 0.25,
                "contracts": [{"symbol": "1OZZ6", "until": "2026-11-20"},
                              {"symbol": "1OZG7", "until": "2027-01-22"}]},
    },
    "broker": {"mode": "dry_run", "account_id": "YOUR_WEBULL_SANDBOX_FUTURES_ACCOUNT_ID", "fill_wait_s": 15},
    # Scheduled-news guard (events.json): no NEW trades around big releases, close an open trade
    # a few minutes before them - price can jump straight over a stop-loss on a release.
    "news": {"enabled": True, "flatten_before_min": 5},
    "daily_loss_cap_pct": 15.0,        # of the starting balance, realized, per ET day
    "halt_below_equity_pct": 50.0,     # of the starting balance
    "stale_after_minutes": 20,         # entries older than this are not taken
    "max_position_hours": 30,
    "summary_time_et": "17:30",
    "allowed_symbols": ["COMEX:GC1!", "COMEX:MGC1!", "COMEX_MINI:MGC1!"],
    "allowed_roots": ["GC", "MGC"],     # any exchange prefix is fine as long as the root matches
    "listen_host": "127.0.0.1",
    "listen_port": 8787,
}

TRADE_COLS = ["tid", "symbol", "direction", "qty", "opened_et", "closed_et", "entry_signal",
              "entry_fill", "exit_kind", "exit_signal", "exit_fill", "points", "gross_usd",
              "cost_usd", "net_usd", "balance_after", "risk_usd", "r_multiple", "late_exit",
              "stage_reached"]


# --------------------------------------------------------------------------- config/state
def load_config(home):
    cfg = json.loads(json.dumps(DEFAULTS))
    p = os.path.join(home, "config.json")
    if os.path.exists(p):
        user = json.load(open(p))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    cfg["news_events"] = _load_events(home)
    return cfg


def _load_events(home):
    p = os.path.join(home, "events.json")
    out = []
    try:
        for e in json.load(open(p)).get("events", []):
            t = datetime.strptime(e["at"], "%Y-%m-%d %H:%M").replace(tzinfo=ET).timestamp()
            out.append({"name": e["name"], "ts": t, "before": int(e.get("before", 30)),
                        "after": int(e.get("after", 30)), "at": e["at"]})
    except (OSError, ValueError, KeyError):
        pass
    return out


def pick_contract(imap, now):
    """Webull contract symbol to use for a NEW trade today (ET date), or None if none is left."""
    if not imap.get("contracts"):
        return imap.get("symbol")
    today = et_date(now)
    for c in sorted(imap["contracts"], key=lambda c: c["until"]):
        if today <= c["until"]:
            return c["symbol"]
    return None


def contract_notice(cfg, now):
    """Plain-English heads-up when the traded contract is about to stop being used (once a day)."""
    from datetime import timedelta
    imap = next(iter(cfg.get("instrument_map", {}).values()), None)
    if not imap or not imap.get("contracts"):
        return None
    today = et_date(now)
    cs = sorted(imap["contracts"], key=lambda c: c["until"])
    cur = next((c for c in cs if today <= c["until"]), None)
    if cur is None:
        return M.warn("no gold contract is configured any more - new trades will be skipped until "
                      "a later contract is added to config.json (instrument_map).")
    days = (datetime.strptime(cur["until"], "%Y-%m-%d") - datetime.strptime(today, "%Y-%m-%d")).days
    if days <= 5:
        nxt = next((c for c in cs if c["until"] > cur["until"]), None)
        tail = f"New trades switch to {nxt['symbol']}." if nxt else "NO later contract is configured - add one."
        return M.warn(f"contract {cur['symbol']} is used for new trades until {cur['until']} "
                      f"({days} days). {tail}")
    return None


def news_window(cfg, now):
    """The scheduled event whose pause window contains `now`, else None."""
    if not cfg.get("news", {}).get("enabled", True):
        return None
    for e in cfg.get("news_events", []):
        if e["ts"] - e["before"] * 60 <= now <= e["ts"] + e["after"] * 60:
            return e
    return None


def news_flatten_due(state, cfg, now):
    """(event) if an open trade should be closed now because a release is minutes away."""
    pos = state.get("position")
    if not pos or pos.get("news_flat") or not cfg.get("news", {}).get("enabled", True):
        return None
    lead = cfg["news"].get("flatten_before_min", 5) * 60
    for e in cfg.get("news_events", []):
        if e["ts"] - lead <= now < e["ts"]:
            return e
    return None


def new_state(cfg):
    return {
        "cash": cfg["account_start_usd"], "position": None, "halted": "",
        "seen": {}, "skipped": {},
        "day": {"date": "", "realized": 0.0, "trades": 0, "wins": 0, "signals": 0, "dropped": 0},
        "total": {"trades": 0, "wins": 0, "net": 0.0, "peak": cfg["account_start_usd"], "max_dd": 0.0,
                  "loss_run": 0, "max_loss_run": 0},
        "last_event_ts": 0, "last_summary_date": "",
    }


def et_date(ts):
    return datetime.fromtimestamp(ts, ET).strftime("%Y-%m-%d")


def et_stamp(ts):
    return datetime.fromtimestamp(ts, ET).strftime("%Y-%m-%d %H:%M:%S")


def roll_day(state, now):
    today = et_date(now)
    if state["day"]["date"] != today:
        state["day"] = {"date": today, "realized": 0.0, "trades": 0, "wins": 0, "signals": 0, "dropped": 0}
    return today


def root_symbol(sym):
    m = re.search(r":([A-Z]+)\d*!?$", sym or "")
    return m.group(1) if m else (sym or "")


def tf_minutes(tf):
    tf = str(tf or "")
    if tf.isdigit():
        return int(tf)
    if tf.endswith("S") and tf[:-1].isdigit():
        return int(tf[:-1]) / 60
    return 0   # daily and up: staleness is not meaningful


def slip_pts(cfg):
    return cfg["slippage_ticks"] * cfg["tick_size"]


def daily_cap_usd(cfg):
    return cfg["account_start_usd"] * cfg["daily_loss_cap_pct"] / 100.0


def margin_needed(cfg, root, price, pv, qty):
    m = cfg["margin"].get(root, {})
    day = m.get("intraday_usd")
    night = m.get("overnight_pct_of_notional", 0.05) * price * pv * qty
    return (day * qty if day is not None else None), night


def _prune(d, keep=400):
    if len(d) > keep:
        for k in sorted(d, key=d.get)[: len(d) - keep]:
            d.pop(k)


# --------------------------------------------------------------------------- the rules
def handle_payload(state, cfg, payload, now, paused=False):
    """Apply one webhook payload.  Mutates state.  Returns {"messages": [...], "closed": [rows]}."""
    out = {"messages": [], "closed": [], "applied": []}
    roll_day(state, now)
    sym = payload.get("sym", "")
    pv = float(payload.get("pv") or 0)
    bar_close = int(payload.get("bar", 0)) / 1000.0 + tf_minutes(payload.get("tf")) * 60
    age_min = (now - bar_close) / 60.0 if bar_close else 0.0
    stale = bool(tf_minutes(payload.get("tf"))) and age_min > cfg["stale_after_minutes"]
    state["last_event_ts"] = now

    for ev in payload.get("events", []):
        key = f"{ev.get('tid')}:{ev.get('e')}"
        if key in state["seen"]:
            out["applied"].append((key, "duplicate"))
            continue
        state["seen"][key] = now
        kind = ev.get("e")
        if kind == "ENTRY":
            res = _entry(state, cfg, ev, sym, pv, now, stale, age_min, paused, out)
        elif kind in ("TP1", "TP2"):
            res = _target(state, cfg, ev, int(kind[2]), out)
        elif kind == "EXIT":
            res = _exit(state, cfg, ev, now, stale, age_min, out)
        else:
            res = "unknown-event"
            out["messages"].append(M.warn(f"unknown event type {kind!r} received - ignored."))
        out["applied"].append((key, res))
    _prune(state["seen"])
    _prune(state["skipped"])
    return out


def _skip(state, cfg, ev, out, reason, detail=""):
    state["skipped"][str(ev.get("tid"))] = reason
    state["day"]["dropped"] += 1
    out["messages"].append(M.skipped(reason, detail))
    return "skipped:" + reason


def _entry(state, cfg, ev, sym, pv, now, stale, age_min, paused, out):
    state["day"]["signals"] += 1
    d = 1 if ev.get("dir") == "LONG" else -1
    sig = float(ev["px"])
    if (sym not in cfg["allowed_symbols"] and root_symbol(sym) not in cfg.get("allowed_roots", [])) or pv <= 0:
        return _skip(state, cfg, ev, out, "this symbol is not on the allowed list", f"symbol {sym}")
    if stale:
        return _skip(state, cfg, ev, out, "the signal arrived too late",
                     f"It came {age_min:.0f} minutes after the bar closed (limit {cfg['stale_after_minutes']}).")
    if paused or not cfg["enabled"]:
        return _skip(state, cfg, ev, out, "paper trading is paused")
    if state["halted"]:
        return _skip(state, cfg, ev, out, "the system is halted", state["halted"])
    ne = news_window(cfg, now)
    if ne:
        hm = ne["at"][11:]
        return _skip(state, cfg, ev, out, f"news time: {ne['name']} at {hm} ET",
                     f"New trades are paused {ne['before']} minutes before to {ne['after']} minutes after it.")
    if state["position"]:
        return _skip(state, cfg, ev, out, "a paper trade is already open")
    if state["day"]["realized"] <= -daily_cap_usd(cfg):
        return _skip(state, cfg, ev, out, "the daily loss limit was reached",
                     f"Down {M.usd(-state['day']['realized'])} today; limit {M.usd(daily_cap_usd(cfg))}.")
    qty = int(cfg["qty"])
    root = root_symbol(sym)
    imap = cfg.get("instrument_map", {}).get(root)
    broker_symbol = None
    if imap:                                   # trade a different (smaller) contract than the chart
        pv, qty, root = float(imap["pv"]), int(imap.get("qty", qty)), imap["trade_root"]
        broker_symbol = pick_contract(imap, now)
        if broker_symbol is None:
            return _skip(state, cfg, ev, out, "no gold contract is set up for today",
                         "Add the next contract to instrument_map in config.json.")
    day_m, night_m = margin_needed(cfg, root, sig, pv, qty)
    margin_ok = day_m is None or state["cash"] >= day_m
    if cfg["enforce_margin"] and not margin_ok:
        return _skip(state, cfg, ev, out, "the account is too small for the margin",
                     f"A real broker would hold about {M.usd(day_m)}; balance is {M.usd(state['cash'])}.")
    fill = sig + d * slip_pts(cfg)
    sl = float(ev["sl"])
    risk = abs(fill - sl) * pv * qty
    worst = risk + slip_pts(cfg) * pv * qty + 2 * cfg["commission_per_side_usd"] * qty
    pos = {"tid": str(ev["tid"]), "sym": sym, "root": root, "dir": d, "qty": qty, "pv": pv,
           "signal_px": sig, "fill_px": fill, "sl": sl, "sl0": sl,
           "tp1": float(ev["tp1"]), "tp2": float(ev["tp2"]), "tp3": float(ev["tp3"]),
           "stage": 0, "opened_ts": now, "conf": ev.get("conf"), "tag": ev.get("tag", ""),
           "risk_usd": risk, "margin_ok": margin_ok, "overdue_warned": False,
           "broker_symbol": broker_symbol}
    state["position"] = pos
    if broker_symbol:
        out.setdefault("intents", []).append({
            "op": "open", "tid": pos["tid"], "root": root, "symbol": broker_symbol, "dir": d,
            "qty": qty, "sig_px": sig, "sl_sig": sl, "tick": (imap or {}).get("tick", cfg["tick_size"])})
    out["messages"].append(M.entry_opened(pos, cfg, day_m, night_m, worst, state["cash"], margin_ok))
    return "opened"


def _target(state, cfg, ev, stage, out):
    pos = state["position"]
    if not pos or pos["tid"] != str(ev.get("tid")):
        if str(ev.get("tid")) in state["skipped"]:
            return "ignored:trade-was-skipped"
        out["messages"].append(M.warn(f"target {stage} signal for a trade the ledger does not have (id {ev.get('tid')})."))
        return "unknown-trade"
    if stage <= pos["stage"]:
        return "ignored:old-stage"
    pos["stage"] = stage
    pos["sl"] = float(ev.get("new_sl", pos["sl"]))
    locked = (pos["sl"] - pos["fill_px"]) * pos["dir"] * pos["pv"] * pos["qty"] - 2 * cfg["commission_per_side_usd"] * pos["qty"]
    out["messages"].append(M.target_hit(pos, stage, pos["sl"], locked))
    if pos.get("broker_symbol"):
        out.setdefault("intents", []).append({
            "op": "stop", "tid": pos["tid"],
            "offset": round((pos["sl"] - pos["signal_px"]) * pos["dir"], 2)})
    return f"stage{stage}"


def _exit(state, cfg, ev, now, stale, age_min, out):
    pos = state["position"]
    tid = str(ev.get("tid"))
    if not pos or pos["tid"] != tid:
        if tid in state["skipped"]:
            return "ignored:trade-was-skipped"
        out["messages"].append(M.warn(f"exit signal for a trade the ledger does not have (id {tid}). "
                                      f"Nothing was changed."))
        return "unknown-trade"
    kind = ev.get("kind", "")
    sig = float(ev["px"])
    s = slip_pts(cfg)
    if kind in LIMIT_KINDS:
        fill = sig
    else:
        fill = sig - pos["dir"] * s          # adverse for both stop and market exits
    pts = (fill - pos["fill_px"]) * pos["dir"]
    gross = pts * pos["pv"] * pos["qty"]
    cost = 2 * cfg["commission_per_side_usd"] * pos["qty"]
    net = gross - cost
    state["cash"] += net
    r_dist = abs(pos["fill_px"] - pos["sl0"])
    row = {"tid": pos["tid"], "symbol": pos["sym"], "direction": "LONG" if pos["dir"] == 1 else "SHORT",
           "qty": pos["qty"], "opened_et": et_stamp(pos["opened_ts"]), "closed_et": et_stamp(now),
           "entry_signal": pos["signal_px"], "entry_fill": round(pos["fill_px"], 2), "exit_kind": kind,
           "exit_signal": sig, "exit_fill": round(fill, 2), "points": round(pts, 2),
           "gross_usd": round(gross, 2), "cost_usd": round(cost, 2), "net_usd": round(net, 2),
           "balance_after": round(state["cash"], 2), "risk_usd": round(pos["risk_usd"], 2),
           "r_multiple": round(pts / r_dist, 3) if r_dist else "", "late_exit": int(stale),
           # how far the trade got: 0 = no target, 1 = TP1, 2 = TP2 (TP3 exit counts as 2 + final)
           "stage_reached": 2 if kind == "TP3" else pos.get("stage", 0)}
    out["closed"].append(row)
    t, dday = state["total"], state["day"]
    t["trades"] += 1
    t["net"] += net
    t["wins"] += 1 if net > 0 else 0
    t["peak"] = max(t["peak"], state["cash"])
    t["max_dd"] = max(t["max_dd"], t["peak"] - state["cash"])
    t["loss_run"] = t["loss_run"] + 1 if net <= 0 else 0
    t["max_loss_run"] = max(t["max_loss_run"], t["loss_run"])
    dday["realized"] += net
    dday["trades"] += 1
    dday["wins"] += 1 if net > 0 else 0
    held = (now - pos["opened_ts"]) / 60.0
    msg = M.trade_closed(pos, kind, fill, net, state["cash"], cfg, held)
    if stale:
        msg += f"\n⚠️ This exit signal came {age_min:.0f} minutes late, so the real fill may differ."
    extra = []
    if dday["realized"] <= -daily_cap_usd(cfg):
        extra.append(f"Daily loss limit reached ({M.usd(daily_cap_usd(cfg))}). No more new paper trades today.")
    floor = cfg["account_start_usd"] * cfg["halt_below_equity_pct"] / 100.0
    if state["cash"] <= floor and not state["halted"]:
        state["halted"] = f"balance {M.usd(state['cash'])} fell to the halt level {M.usd(floor)}"
        extra.append("Paper trading is HALTED: " + state["halted"] + ". Run 'resume' after reviewing.")
    if extra:
        msg += "\n🛑 " + " ".join(extra)
    out["messages"].append(msg)
    if pos.get("broker_symbol"):
        out.setdefault("intents", []).append({"op": "close", "tid": pos["tid"], "kind": kind})
    state["position"] = None
    return "closed"


def reconcile(state, cfg, price, now):
    """Close the open ledger position by hand at `price` (receiver CLI)."""
    pos = state["position"]
    if not pos:
        return {"messages": ["no open position"], "closed": []}
    ev = {"e": "EXIT", "tid": pos["tid"], "kind": "MANUAL", "px": price}
    return handle_payload(state, cfg, {"sym": pos["sym"], "pv": pos["pv"], "tf": "5", "bar": 0,
                                       "events": [ev]}, now)


def overdue(state, cfg, now):
    """Message if the open trade has been open longer than max_position_hours (once)."""
    pos = state["position"]
    if not pos or pos.get("overdue_warned"):
        return None
    hours = (now - pos["opened_ts"]) / 3600.0
    if hours < cfg["max_position_hours"]:
        return None
    pos["overdue_warned"] = True
    return M.warn(f"a paper trade has been open for {hours:.0f} hours with no exit signal. "
                  f"The exit alert may have been missed - check the chart, then run 'reconcile PRICE'.")


def summary(state, cfg, now):
    roll_day(state, now)
    d = datetime.fromtimestamp(now, ET).strftime("%a %d %b")
    return M.daily_summary(d, state["day"], state["total"], state["position"], state["cash"], cfg,
                           state["day"]["signals"], state["day"]["dropped"])


def append_trades(home, rows):
    p = os.path.join(home, "trades.csv")
    new = not os.path.exists(p)
    with open(p, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_COLS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow(r)
