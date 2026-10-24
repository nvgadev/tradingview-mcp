#!/usr/bin/env python3
"""End-to-end check: start a throw-away receiver (own state dir, own port, stdout instead of
Telegram), post the same JSON shape the indicator sends, and verify what the ledger did.
Never touches the real state or sends a real message.   usage: simulate.py"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8799
SECRET = "test-secret-0123456789abcdef"


def post(body, raw=False):
    data = body if raw else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/hook", data, {"Content-Type": "text/plain"})
    try:
        r = urllib.request.urlopen(req, timeout=5)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main():
    home = tempfile.mkdtemp(prefix="gcp-sim-")
    open(os.path.join(home, "secret.env"), "w").write(f"WEBHOOK_SECRET={SECRET}\n")
    json.dump({"listen_port": PORT}, open(os.path.join(home, "config.json"), "w"))
    env = dict(os.environ, GCP_HOME=home, GCP_NOTIFY="stdout", GCP_PORT=str(PORT), PYTHONUNBUFFERED="1")
    log = open(os.path.join(home, "server.log"), "w")
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "receiver.py"), "serve"], env=env,
                           stdout=log, stderr=subprocess.STDOUT)
    fails = []

    def check(name, cond, extra=""):
        print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if not cond else ""))
        if not cond:
            fails.append(name)

    try:
        for _ in range(40):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=1).read()
                break
            except Exception:
                time.sleep(0.15)
        now = int(time.time())
        bar = (now - 10 * 60) * 1000          # bar opened 10 min ago, tf 5 -> closed 5 min ago
        base = {"secret": SECRET, "sym": "COMEX:GC1!", "tf": "5", "bar": bar, "pv": 100}
        tid = str(now * 1000)

        st, _ = post({**base, "secret": "nope", "events": []})
        check("wrong secret -> 403", st == 403, st)
        st, _ = post(b"not json", raw=True)
        check("bad json -> 400", st == 400, st)

        ent = {"e": "ENTRY", "tid": tid, "dir": "LONG", "px": 4178.3, "sl": 4171.6, "tp1": 4185.0,
               "tp2": 4191.7, "tp3": 4198.4, "conf": 8, "tag": ""}
        st, body = post({**base, "events": [ent]})
        check("entry accepted", st == 200 and "opened" in body, body)
        st, body = post({**base, "events": [ent]})
        check("duplicate entry ignored", st == 200 and "duplicate" in body, body)
        st, body = post({**base, "bar": bar + 300000, "events": [
            {"e": "TP1", "tid": tid, "px": 4185.0, "new_sl": 4179.6}]})
        check("TP1 moves stop", "stage1" in body, body)
        st, body = post({**base, "bar": bar + 600000, "events": [
            {"e": "EXIT", "tid": tid, "dir": "LONG", "kind": "TP1LOCK", "px": 4179.6}]})
        check("exit closes trade", "closed" in body, body)

        state = json.load(open(os.path.join(home, "state.json")))
        check("no open position", state["position"] is None)
        check("one trade booked", state["total"]["trades"] == 1, state["total"])
        rows = open(os.path.join(home, "trades.csv")).read().strip().splitlines()
        check("trades.csv has header+1 row", len(rows) == 2, rows)
        ev = open(os.path.join(home, "events.jsonl")).read()
        check("secret never written to events log", SECRET not in ev)
        print("\n--- server log ---")
        time.sleep(0.3)
    finally:
        srv.terminate()
        srv.wait(timeout=5)
        log.close()
    print(open(os.path.join(home, "server.log")).read())
    print("trades.csv:\n" + open(os.path.join(home, "trades.csv")).read())
    print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
    sys.exit(1 if fails else 0)


main()
