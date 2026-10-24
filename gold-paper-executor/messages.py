"""Telegram text for the GC paper system.  Pure functions, stdlib only.

Rules (the user asked for these - see report-plain-language): no R multiples, no profit
factor, no abbreviations without a meaning.  Money in dollars, prices in points, and every
loss says how big it is against the account.
"""

KIND_TEXT = {
    "SL":      "price hit the stop-loss",
    "BE":      "price came back to the protected stop, so the trade ended about even",
    "TP1LOCK": "price came back to the stop after target 2 had been reached, so most of the profit was kept",
    "TP3":     "price reached the final target",
    "FLIP":    "the trend turned the other way, so the trade was closed",
    "SESSION": "the trading session ended, so the trade was closed",
    "MANUAL":  "closed by hand in the ledger",
    "NEWS":    "closed before a scheduled news release, because price can jump over a stop-loss",
}


def px(x):
    return f"{x:,.2f}"


PRODUCT = {"1OZ": "1-ounce gold", "MGC": "micro gold", "GC": "gold"}


def usd(x, sign=False):
    s = f"{abs(x):,.0f}"
    if sign:
        return ("+$" if x >= 0 else "-$") + s
    return ("-$" if x < 0 else "$") + s


def side_word(d):
    return "BUY (long)" if d == 1 else "SELL (short)"


def entry_opened(pos, cfg, margin_day, margin_night, worst_loss, balance, margin_ok):
    name = PRODUCT.get(pos.get("root", ""), "gold")
    lines = [
        f"📈 GOLD paper trade opened: {side_word(pos['dir'])} {pos['qty']} x {name} at {px(pos['fill_px'])}",
        f"Stop-loss {px(pos['sl'])}  (if it is hit you lose about {usd(worst_loss)}, "
        f"{worst_loss / cfg['account_start_usd'] * 100:.1f}% of the account)",
        f"Targets: {px(pos['tp1'])}, {px(pos['tp2'])}, {px(pos['tp3'])}",
    ]
    if margin_day is not None:
        need = max(margin_day, margin_night)
        if margin_ok:
            lines.append(f"Margin a real broker would hold: about {usd(need)}. Your balance {usd(balance)} covers it.")
        else:
            lines.append(f"⚠️ A real broker would hold about {usd(need)} for this. Your balance is {usd(balance)}, "
                         f"so a real account could NOT take this trade. Paper only.")
    return "\n".join(lines)


def target_hit(pos, stage, new_sl, locked_usd):
    name = {1: "target 1", 2: "target 2"}[stage]
    tail = (f"Stop moved to {px(new_sl)} - if price turns back now you still keep about {usd(locked_usd)}."
            if locked_usd > 0 else f"Stop moved to {px(new_sl)}.")
    return f"🎯 GOLD paper trade reached {name} ({px(pos['tp%d' % stage])}). {tail}"


def trade_closed(pos, kind, exit_fill, net_usd, balance, cfg, held_minutes):
    even = abs(net_usd) <= 0.1 * float(pos.get("risk_usd") or 0)
    if even:
        head = "➖ GOLD paper trade closed about even: " + usd(net_usd, sign=True)
    elif net_usd >= 0:
        head = "✅ GOLD paper trade closed with a profit: " + usd(net_usd, sign=True)
    else:
        head = "❌ GOLD paper trade closed with a loss: " + usd(net_usd, sign=True)
    side = "BUY" if pos["dir"] == 1 else "SELL"
    why = KIND_TEXT.get(kind, kind)
    h, m = divmod(int(held_minutes), 60)
    held = f"{h}h {m}m" if h else f"{m}m"
    return "\n".join([
        head,
        f"{side} {px(pos['fill_px'])} -> {px(exit_fill)}: {why}. Held {held}.",
        f"Account balance {usd(balance)} (started {usd(cfg['account_start_usd'])}).",
    ])


def skipped(reason, detail=""):
    return f"⏭ GOLD signal NOT taken: {reason}" + (f"\n{detail}" if detail else "")


def warn(text):
    return f"⚠️ GOLD paper system: {text}"


def daily_summary(date_txt, today, total, state_pos, balance, cfg, signals_today, dropped_today):
    t = today
    if t["trades"] == 0:
        trade_line = "No trades closed today."
    else:
        trade_line = (f"Trades closed today: {t['trades']} ({t['wins']} won, {t['trades'] - t['wins']} lost), "
                      f"net {usd(t['realized'], sign=True)}.")
    lines = [f"📊 GOLD paper account, {date_txt}", trade_line]
    if signals_today == 0:
        lines.append("No signals came in today.")
    elif dropped_today:
        lines.append(f"⚠️ {dropped_today} signal(s) were not taken - see the earlier messages.")
    if state_pos:
        d = "BUY" if state_pos["dir"] == 1 else "SELL"
        lines.append(f"Open now: {d} from {px(state_pos['fill_px'])}, stop {px(state_pos['sl'])}.")
    lines.append(f"Balance {usd(balance)} (started {usd(cfg['account_start_usd'])}); "
                 f"all trades so far {total['trades']}, net {usd(total['net'], sign=True)}, "
                 f"deepest drop {usd(total['max_dd'])}.")
    imap = next(iter(cfg.get("instrument_map", {}).values()), None)
    if imap:
        per = float(imap["pv"]) * int(imap.get("qty", 1))
        lines.append(f"(Each trade is {imap.get('qty', 1)} x {PRODUCT.get(imap['trade_root'], 'gold')}: "
                     f"about {usd(per)} gained or lost for every $1 move in gold.)")
    return "\n".join(lines)


def status_text(state, cfg):
    p = state.get("position")
    out = [f"balance {usd(state['cash'])}  trades {state['total']['trades']}  "
           f"net {usd(state['total']['net'], sign=True)}  deepest drop {usd(state['total']['max_dd'])}"]
    out.append("halted: " + state["halted"] if state.get("halted") else "running")
    if p:
        out.append(f"open: {side_word(p['dir'])} {p['qty']} @ {px(p['fill_px'])} stop {px(p['sl'])} "
                   f"(tid {p['tid']})")
    else:
        out.append("no open trade")
    return "\n".join(out)


def news_flatten(name, hm):
    return (f"📰 Closing the open gold trade now: {name} comes out at {hm} ET. "
            f"Prices can jump straight over a stop-loss at a release, so it is safer to be out.")
