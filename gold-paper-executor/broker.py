"""Webull futures PAPER broker adapter.

Turns the ledger's intents (open / stop / close) into Webull sandbox orders.  Safety:
  * mode "off"     - does nothing
  * mode "dry_run" - logs what it WOULD send, sends nothing (default)
  * mode "paper"   - real calls, but ONLY against api.sandbox.webull.com and ONLY for the
                     configured sandbox futures account, and only after broker_check.py
                     has verified the cancel/detail call forms (broker_forms.json).
No order types beyond MARKET and STOP_LOSS are used (Webull has no OCO/bracket for futures,
so this module manages the protective stop itself).
"""
import json
import os
import time

import messages as M
import webull_lite as W

PLACE = "/trading/orders/place"
DETAIL = "/trading/orders/get"
CANCEL = "/trading/orders/cancel"
NAMES = {"MGC": "Micro Gold", "GC": "Gold", "1OZ": "1-Ounce Gold"}

STATUS_KEYS = ["order_status", "status", "orderStatus"]
FILL_PX_KEYS = ["filled_price", "avg_filled_price", "average_filled_price", "avg_price",
                "average_price", "last_filled_price", "filled_avg_price", "avgFilledPrice"]
FILL_QTY_KEYS = ["filled_quantity", "filled_qty", "total_filled_quantity", "filledQuantity"]


def dig(obj, names):
    """First value for any of `names` (priority order), searched breadth-first."""
    for n in names:
        queue = [obj]
        while queue:
            o = queue.pop(0)
            if isinstance(o, dict):
                for k, v in o.items():
                    if k.lower() == n.lower() and v not in (None, ""):
                        return v
                queue.extend(v for v in o.values() if isinstance(v, (dict, list)))
            elif isinstance(o, list):
                queue.extend(o)
    return None


def status_kind(s):
    s = str(s or "").upper()
    if "PARTIAL" in s:
        return "partial"
    if "FILLED" in s:
        return "filled"
    if any(w in s for w in ("CANCEL", "REJECT", "FAIL", "EXPIRE")):
        return "dead"
    return "working"


def rnd(x, tick=0.1):
    return round(round(x / tick) * tick, 2)


def px(x):
    return f"{x:.2f}"


class Broker:
    def __init__(self, bcfg, home, wb=None, log=print, sleep=time.sleep):
        self.cfg, self.home, self.wb, self.log, self.sleep = bcfg, home, wb, log, sleep
        self.mode = bcfg.get("mode", "dry_run")
        self.account = bcfg.get("account_id", "")
        self.fill_wait = float(bcfg.get("fill_wait_s", 15))
        self.forms = self._load("broker_forms.json", {})
        self.state = self._load("broker_state.json", {})
        self._acct_ok = False

    # ------------------------------------------------------------------ files
    def _path(self, name):
        return os.path.join(self.home, name)

    def _load(self, name, default):
        try:
            return json.load(open(self._path(name)))
        except (OSError, ValueError):
            return default

    def _save(self):
        tmp = self._path("broker_state.json.tmp")
        json.dump(self.state, open(tmp, "w"), indent=1)
        os.replace(tmp, self._path("broker_state.json"))

    # ------------------------------------------------------------------ guards
    def ready(self):
        if self.mode == "off":
            return False, "broker is switched off"
        if self.mode == "dry_run":
            return True, ""
        if self.mode != "paper":
            return False, f"unknown broker mode {self.mode!r}"
        if self.wb is None or not self.wb.is_sandbox:
            return False, "not connected to the Webull sandbox"
        if not self.forms.get("verified"):
            return False, "broker_check.py has not been run yet"
        if not self._acct_ok:
            try:
                accts = self.wb.get("/openapi/account/list")
            except W.WebullError as e:
                return False, f"account check failed ({e})"
            ok = any(a.get("account_id") == self.account and a.get("account_class") == "FUTURES"
                     for a in accts)
            if not ok:
                return False, "configured account is not a sandbox FUTURES account"
            self._acct_ok = True
        return True, ""

    # ------------------------------------------------------------------ calls
    def _place(self, cid, symbol, side, otype, qty, tif="DAY", **extra):
        o = {"combo_type": "NORMAL", "client_order_id": cid, "symbol": symbol,
             "instrument_type": "FUTURES", "market": "US", "order_type": otype,
             "quantity": str(qty), "side": side, "time_in_force": tif, "entrust_type": "QTY"}
        o.update({k: str(v) for k, v in extra.items()})
        return self.wb.post(PLACE, {"account_id": self.account, "new_orders": [o]})

    def _detail(self, cid):
        param = self.forms.get("detail_param", "client_order_id")
        return self.wb.get(DETAIL, account_id=self.account, **{param: cid})

    def _cancel(self, cid):
        f = self.forms.get("cancel", {"method": "DELETE", "where": "body"})
        body = {"account_id": self.account, "client_order_id": cid}
        if f.get("where") == "query":
            return self.wb.request(f.get("method", "DELETE"), CANCEL, queries=body)
        return self.wb.request(f.get("method", "DELETE"), CANCEL, body=body)

    def _wait_fill(self, cid):
        end = time.time() + self.fill_wait
        while True:
            try:
                d = self._detail(cid)
                kind = status_kind(dig(d, STATUS_KEYS))
                price = dig(d, FILL_PX_KEYS)
                if kind == "filled":
                    if price is not None:
                        return float(price), "filled"
                    return None, "filled_no_price"        # filled, but the price field is unknown
                if kind == "dead":
                    return None, "dead"
            except (W.WebullError, ValueError):
                pass
            if time.time() >= end:
                return None, "timeout"
            self.sleep(1)

    # ------------------------------------------------------------------ ops
    def run(self, it):
        """Execute one intent.  Returns a list of plain-English Telegram messages."""
        ok, why = self.ready()
        if not ok:
            return [M.warn(f"Webull paper order NOT sent ({why}).")]
        try:
            return getattr(self, "_op_" + it["op"])(it)
        except W.WebullError as e:
            self.log(f"broker error: {e}")
            return [M.warn("Webull paper account call failed - check the account and the log. "
                           f"({str(e)[:140]})")]

    def _op_open(self, it):
        tid, sym, d, q = it["tid"], it["symbol"], it["dir"], it["qty"]
        if self.state.get(tid, {}).get("status") in ("open", "dry"):
            return []
        side, opp = ("BUY", "SELL") if d == 1 else ("SELL", "BUY")
        dist = abs(it["sig_px"] - it["sl_sig"])
        if self.mode == "dry_run":
            self.state[tid] = {"status": "dry", "symbol": sym, "dir": d, "qty": q}
            self._save()
            self.log(f"[dry-run] would {side} {q} {sym} at market, stop {dist:.1f} points away")
            return []
        cid = f"gcE{tid}"
        self._place(cid, sym, side, "MARKET", q)
        fill, why = self._wait_fill(cid)
        approx = False
        if fill is None and why == "filled_no_price":      # still place the stop; use the signal price
            fill, approx = it["sig_px"], True
        if fill is None:
            self.state[tid] = {"status": "unconfirmed", "symbol": sym, "dir": d, "qty": q}
            self._save()
            return [M.warn(f"the {side} order for {sym} was sent but the fill could not be confirmed ({why}). "
                           f"Check the Webull paper account by hand.")]
        tick = float(it.get("tick") or 0.1)
        stop = rnd(fill - d * dist, tick)
        scid = f"gcS{tid}0"
        st = {"status": "open", "symbol": sym, "dir": d, "qty": q, "entry_fill": fill, "tick": tick,
              "stop": stop, "stop_cid": scid, "stop_seq": 0, "opened": int(time.time())}
        try:
            self._place(scid, sym, opp, "STOP_LOSS", q, tif="GTC", stop_price=px(stop))
        except W.WebullError:
            self._place(f"gcX{tid}", sym, opp, "MARKET", q)      # never stay without a stop
            st["status"] = "closed"
            self.state[tid] = st
            self._save()
            return [M.warn(f"bought {q} {sym} at {px(fill)} but the stop order was refused, so the "
                           f"position was closed again immediately.")]
        self.state[tid] = st
        self._save()
        name = NAMES.get(it.get("root", ""), "Gold")
        note = " (fill price not reported by Webull - stop is based on the signal price)" if approx else ""
        return [f"🟢 Webull paper account: {side} {q} {name} ({sym}) filled at {px(fill)}{note}. "
                f"Safety stop order placed at {px(stop)}."]

    def _op_stop(self, it):
        tid = it["tid"]
        st = self.state.get(tid)
        if not st or st.get("status") not in ("open", "dry"):
            return []
        if st["status"] == "dry":
            self.log(f"[dry-run] would move stop to entry+{it['offset']:.1f} points")
            return []
        d = st["dir"]
        new_stop = rnd(st["entry_fill"] + d * it["offset"], st.get("tick", 0.1))
        if (new_stop - st["stop"]) * d <= 0:           # a stop only ever tightens
            return []
        opp = "SELL" if d == 1 else "BUY"
        seq = st["stop_seq"] + 1
        ncid = f"gcS{tid}{seq}"
        self._place(ncid, st["symbol"], opp, "STOP_LOSS", st["qty"], tif="GTC", stop_price=px(new_stop))
        old = st["stop_cid"]
        st.update(stop=new_stop, stop_cid=ncid, stop_seq=seq)
        self._save()
        try:
            self._cancel(old)
        except W.WebullError as e:
            return [M.warn(f"new stop placed at {px(new_stop)} but the old stop could not be cancelled "
                           f"({str(e)[:100]}). Check the Webull paper account.")]
        return [f"🛡 Webull paper account: safety stop moved to {px(new_stop)}."]

    def _op_close(self, it):
        tid = it["tid"]
        st = self.state.get(tid)
        if not st or st.get("status") not in ("open", "dry"):
            return []
        if st["status"] == "dry":
            st["status"] = "closed"
            self._save()
            self.log(f"[dry-run] would close {st['symbol']} ({it.get('kind')})")
            return []
        opp = "SELL" if st["dir"] == 1 else "BUY"
        hit = self._stop_fill(st["stop_cid"], st["stop"])
        if hit is not None:
            st["status"] = "closed"
            self._save()
            return [f"🔴 Webull paper account: trade ended - the safety stop was hit at {px(hit)}."]
        try:
            self._cancel(st["stop_cid"])
        except W.WebullError:
            pass
        hit = self._stop_fill(st["stop_cid"], st["stop"])   # filled while we were cancelling?
        if hit is not None:
            st["status"] = "closed"
            self._save()
            return [f"🔴 Webull paper account: trade ended - the safety stop was hit at {px(hit)}."]
        if self._position_qty(st["symbol"]) == 0:        # flat already: never open the other side
            st["status"] = "closed"
            self._save()
            return [M.warn(f"{st['symbol']} was already flat at Webull when the exit signal came, "
                           f"so no close order was sent.")]
        cid = f"gcX{tid}"
        self._place(cid, st["symbol"], opp, "MARKET", st["qty"])
        fill, why = self._wait_fill(cid)
        closed = fill is not None or why == "filled_no_price"
        st["status"] = "closed" if closed else "unconfirmed"
        self._save()
        if fill is None and why == "filled_no_price":
            return [f"🔴 Webull paper account: position closed (exit price not reported)."]
        if fill is None:
            return [M.warn(f"close order sent for {st['symbol']} but the fill could not be confirmed ({why}). "
                           f"Check the Webull paper account by hand.")]
        reason = M.KIND_TEXT.get(it.get("kind", ""), "exit signal")
        return [f"🔴 Webull paper account: position closed at {px(fill)} ({reason})."]

    def _stop_fill(self, cid, fallback):
        """Fill price if the stop order has filled (fallback = its own stop price), else None."""
        try:
            d = self._detail(cid)
        except W.WebullError:
            return None
        if status_kind(dig(d, STATUS_KEYS)) == "filled":
            p = dig(d, FILL_PX_KEYS)
            return float(p) if p is not None else float(fallback)
        return None

    def _position_qty(self, symbol):
        """Open quantity for `symbol` in the paper account, or None if it cannot be read."""
        try:
            rows = self.wb.get("/openapi/assets/positions", account_id=self.account)
        except W.WebullError:
            return None
        if not isinstance(rows, list):
            rows = dig(rows, ["positions", "items", "data"]) or []
        if not rows:
            return 0
        total = 0.0
        for r in rows:
            if str(dig(r, ["symbol", "ticker"])) == symbol:
                try:
                    total += abs(float(dig(r, ["quantity", "qty", "position", "position_qty"]) or 0))
                except (TypeError, ValueError):
                    return None
        return total
