#!/usr/bin/env python3
"""One-time check of the Webull SANDBOX (paper) order calls.  Run it yourself:

    python3 broker_check.py --yes

What it does (paper money only, sandbox host only, futures sandbox account only):
  1. places ONE tiny BUY LIMIT order for 1 Micro Gold, 10% below the market (it will not fill)
  2. reads the order back, lists open orders
  3. cancels it, trying the known call shapes until one works
  4. tries one far-away STOP_LOSS order, then cancels that too
  5. writes broker_forms.json (which call shapes work) and broker_check.log (raw replies)
Nothing here can touch a real-money account: the client is pinned to api.sandbox.webull.com.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import broker as B  # noqa: E402
import ledger  # noqa: E402
import webull_lite as W  # noqa: E402

REPO_CLI = "/Users/bittusunand/Desktop/gopi/tradingview-mcp/src/cli/index.js"
LOG = os.path.join(HERE, "broker_check.log")


def out(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    open(LOG, "a").write(line + "\n")


def market_price():
    if "--price" in sys.argv:
        return float(sys.argv[sys.argv.index("--price") + 1])
    try:
        r = subprocess.run(["node", REPO_CLI, "quote"], capture_output=True, text=True, timeout=20)
        return float(json.loads(r.stdout)["last"])
    except Exception:
        sys.exit("Could not read a gold price from TradingView. Re-run with:  --price 4180")


def main():
    if "--yes" not in sys.argv:
        sys.exit("This places (and cancels) one far-away PAPER order. Run again with --yes")
    open(LOG, "w").write(time.strftime("broker_check %Y-%m-%d %H:%M:%S\n"))
    cfg = ledger.load_config(HERE)
    acct = cfg["broker"]["account_id"]
    sym = cfg["instrument_map"]["GC"]["symbol"]
    wb = W.Webull()
    if not wb.is_sandbox:
        sys.exit("REFUSING: client is not the sandbox")
    accts = wb.get("/openapi/account/list")
    if not any(a.get("account_id") == acct and a.get("account_class") == "FUTURES" for a in accts):
        sys.exit("REFUSING: configured account is not a sandbox FUTURES account")
    price = market_price()
    px = B.rnd(price * 0.90)
    out(f"sandbox futures account ok | contract {sym} | market ~{price} | test limit {px}")

    forms = {"verified": False}
    stamp = int(time.time())
    br = B.Broker({"mode": "paper", "account_id": acct}, HERE, wb=wb)

    # 1-2. place + read back
    cid = f"gcCHK{stamp}"
    r = br._place(cid, sym, "BUY", "LIMIT", 1, tif="DAY", limit_price=B.px(px))
    out("PLACE ->", json.dumps(r)[:500])
    time.sleep(1.5)
    detail_ok = None
    for param in ("client_order_id", "order_id"):
        val = cid if param == "client_order_id" else B.dig(r, ["order_id", "orderId"])
        if not val:
            continue
        try:
            d = wb.get(B.DETAIL, account_id=acct, **{param: val})
            out(f"DETAIL by {param} ->", json.dumps(d)[:700])
            out("   status:", B.dig(d, B.STATUS_KEYS), "| kind:", B.status_kind(B.dig(d, B.STATUS_KEYS)))
            detail_ok = param
            break
        except W.WebullError as e:
            out(f"DETAIL by {param} failed:", str(e)[:200])
    try:
        o = wb.get("/trading/orders/open-orders/list", account_id=acct, page_size=10)
        out("OPEN ORDERS ->", json.dumps(o)[:500])
    except W.WebullError as e:
        out("open orders failed:", str(e)[:200])

    # 3. cancel, trying the plausible shapes
    cancel_ok = None
    for method, where in (("DELETE", "body"), ("DELETE", "query"), ("POST", "body"), ("POST", "query")):
        br.forms = {"cancel": {"method": method, "where": where}, "detail_param": detail_ok or "client_order_id"}
        try:
            c = br._cancel(cid)
            out(f"CANCEL {method}/{where} ->", json.dumps(c)[:300])
        except W.WebullError as e:
            out(f"CANCEL {method}/{where} failed:", str(e)[:200])
            continue
        time.sleep(1.5)
        try:
            kind = B.status_kind(B.dig(br._detail(cid), B.STATUS_KEYS))
        except W.WebullError:
            kind = "?"
        out("   status after cancel:", kind)
        if kind == "dead":
            cancel_ok = {"method": method, "where": where}
            break

    # 4. far-away stop order (what the executor places after a fill)
    stop_ok = False
    scid = f"gcCHKs{stamp}"
    try:
        s = br._place(scid, sym, "SELL", "STOP_LOSS", 1, tif="GTC", stop_price=B.px(B.rnd(price * 0.90)))
        out("STOP PLACE ->", json.dumps(s)[:400])
        stop_ok = True
        time.sleep(1.5)
        if cancel_ok:
            try:
                br._cancel(scid)
                out("STOP cancelled")
            except W.WebullError as e:
                out("STOP cancel failed:", str(e)[:200])
    except W.WebullError as e:
        out("STOP PLACE failed (fine if it says there is no position):", str(e)[:300])

    # 5. verdict
    left = wb.get("/trading/orders/open-orders/list", account_id=acct, page_size=10)
    left_n = len(left) if isinstance(left, list) else len(B.dig(left, ["orders", "items", "data"]) or [])
    forms.update(detail_param=detail_ok, cancel=cancel_ok, stop_without_position_ok=stop_ok,
                 verified=bool(detail_ok and cancel_ok), checked_at=int(time.time()))
    json.dump(forms, open(os.path.join(HERE, "broker_forms.json"), "w"), indent=1)
    out("\nRESULT:", json.dumps(forms))
    out(f"orders still open in the paper account: {left_n}")
    if not forms["verified"]:
        out("NOT VERIFIED - send broker_check.log to Claude.")
    else:
        out("VERIFIED. The executor may now be switched from dry_run to paper.")


main()
