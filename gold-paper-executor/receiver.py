#!/usr/bin/env python3
"""GC paper executor: webhook receiver + ledger + Telegram.   stdlib only.

  receiver.py serve              run the webhook server (launchd does this)
  receiver.py status             print the ledger
  receiver.py halt "reason"      stop taking NEW trades (open trade keeps being managed)
  receiver.py resume             clear a halt
  receiver.py reconcile PRICE    close the open ledger trade by hand at PRICE
  receiver.py summary            print today's summary text (no send)

Env: GCP_HOME (state dir, default = this folder), GCP_NOTIFY=stdout|telegram|none,
     GCP_PORT (override listen port - used by the tests).
Only POST /hook does anything; GET / answers "up" so the tunnel can be checked.
"""
import fcntl
import hmac
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import broker as B  # noqa: E402
import ledger  # noqa: E402
import messages as M  # noqa: E402
import webull_lite as W  # noqa: E402

HOME = os.environ.get("GCP_HOME", HERE)
NOTIFY = os.environ.get("GCP_NOTIFY", "telegram")
TG_ENV = os.path.expanduser("~/.config/premarket/telegram.env")
MAX_BODY = 16384


def repair_body(raw):
    """TradingView's syminfo.tickerid for continuous futures can be a JSON-looking string
    (={"settlement-as-close":true,"symbol":"COMEX_MINI:MGC1!"}) whose quotes break our JSON.
    Pull the plain symbol out of it.  Returns repaired text (unchanged if nothing matched)."""
    txt = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    return re.sub(r'"sym"\s*:\s*"=\{[^}]*?"symbol"\s*:\s*"([^"]+)"[^}]*\}"', r'"sym":"\1"', txt)


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line, flush=True)


def read_env(path):
    out = {}
    try:
        for ln in open(path):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                out[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return out


# ------------------------------------------------------------------ notifications
_q = queue.Queue()
_bq = queue.Queue()      # broker intents, executed one at a time in arrival order


def notify(text):
    _q.put(text)


def _sender():
    while True:
        text = _q.get()
        if NOTIFY == "none":
            continue
        if NOTIFY == "stdout":
            print("[telegram]\n" + text + "\n", flush=True)
            continue
        env = read_env(TG_ENV)
        tok, chat = env.get("TG_TOKEN"), env.get("TG_CHAT_ID")
        if not tok or not chat:
            log("telegram: credentials missing - message not sent")
            continue
        try:
            data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
            urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=10).read()
        except Exception as e:   # never log the URL: it contains the token
            log(f"telegram send failed: {type(e).__name__} {getattr(e, 'code', '')}")


def _broker_worker():
    while True:
        it = _bq.get()
        try:
            for m in APP.broker().run(it):
                notify(m)
        except Exception as e:
            log(f"broker worker error: {type(e).__name__}: {e}")
            notify(M.warn("a Webull paper step crashed. Check the Webull paper account and the log."))


# ------------------------------------------------------------------ state io
def _p(name):
    return os.path.join(HOME, name)


@contextmanager
def locked_state(cfg):
    """Exclusive lock + load; caller mutates the dict; saved on clean exit."""
    os.makedirs(HOME, exist_ok=True)
    with open(_p("state.lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            st = json.load(open(_p("state.json"))) if os.path.exists(_p("state.json")) else ledger.new_state(cfg)
        except (OSError, ValueError):
            os.replace(_p("state.json"), _p("state.json.corrupt"))
            st = ledger.new_state(cfg)
        yield st
        tmp = _p("state.json.tmp")
        json.dump(st, open(tmp, "w"), indent=1)
        os.replace(tmp, _p("state.json"))


def append_event(payload, applied):
    safe = dict(payload)
    safe.pop("secret", None)
    with open(_p("events.jsonl"), "a") as f:
        f.write(json.dumps({"ts": int(time.time()), "payload": safe, "applied": applied}) + "\n")


# ------------------------------------------------------------------ app
class App:
    def __init__(self):
        self.secret = read_env(_p("secret.env")).get("WEBHOOK_SECRET", "")
        if len(self.secret) < 16:
            raise SystemExit("secret.env missing or WEBHOOK_SECRET too short")
        self.bad_secret_notified = 0.0
        self._broker = None

    def broker(self):
        bcfg = self.cfg()["broker"]
        if self._broker is None or self._broker.mode != bcfg.get("mode") \
                or self._broker.account != bcfg.get("account_id"):
            wb = None
            if bcfg.get("mode") == "paper":
                try:
                    wb = W.Webull()
                except W.WebullError as e:
                    log(f"webull client not available: {e}")
            self._broker = B.Broker(bcfg, HOME, wb=wb, log=log)
            log(f"broker mode: {self._broker.mode}")
        return self._broker

    def cfg(self):
        return ledger.load_config(HOME)   # re-read each time: config edits apply without a restart

    def process(self, payload):
        cfg = self.cfg()
        paused = os.path.exists(_p("PAUSE"))
        now = time.time()
        with locked_state(cfg) as st:
            out = ledger.handle_payload(st, cfg, payload, now, paused=paused)
        if out["closed"]:
            ledger.append_trades(HOME, out["closed"])
        append_event(payload, out["applied"])
        for m in out["messages"]:
            notify(m)
        for it in out.get("intents", []):
            _bq.put(it)
        log("event " + ", ".join(f"{k}={v}" for k, v in out["applied"]))
        return out

    def bad_secret(self):
        now = time.time()
        log("webhook rejected: wrong secret")
        if now - self.bad_secret_notified > 3600:
            self.bad_secret_notified = now
            notify(M.warn("a webhook arrived with the wrong password and was ignored. If this was your "
                          "indicator, its 'Webhook secret' input no longer matches - re-enter it and "
                          "recreate the alert."))


APP = None


class Handler(BaseHTTPRequestHandler):
    server_version = "gcpaper"

    def log_message(self, *a):
        pass

    def _reply(self, code, body=b"ok"):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply(200, b"gc-paper receiver up")

    def do_POST(self):
        if self.path.split("?")[0].rstrip("/") not in ("", "/hook"):
            return self._reply(404, b"no")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > MAX_BODY:
            return self._reply(400, b"bad size")
        raw = self.rfile.read(n)
        try:
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = json.loads(repair_body(raw))
                log("webhook body repaired (symbol string had quotes)")
            assert isinstance(payload, dict)
        except Exception:
            log("webhook rejected: body is not a JSON object")
            return self._reply(400, b"bad json")
        if not hmac.compare_digest(str(payload.get("secret", "")).encode(), APP.secret.encode()):
            APP.bad_secret()
            return self._reply(403, b"no")
        try:
            out = APP.process(payload)
        except Exception as e:
            log(f"processing error: {type(e).__name__}: {e}")
            notify(M.warn("a signal arrived but the ledger hit an error and did NOT process it. "
                          "Check the log."))
            return self._reply(500, b"error")
        self._reply(200, json.dumps({"ok": True, "applied": out["applied"]}).encode())


def scheduler():
    """Once a minute: daily summary after the session, overdue-trade check."""
    while True:
        try:
            cfg = APP.cfg()
            now = time.time()
            hhmm = ledger.datetime.fromtimestamp(now, ledger.ET).strftime("%H:%M")
            wd = ledger.datetime.fromtimestamp(now, ledger.ET).weekday()
            with locked_state(cfg) as st:
                today = ledger.roll_day(st, now)
                msg = ledger.overdue(st, cfg, now)
                if msg:
                    notify(msg)
                if st.get("last_contract_notice") != today:
                    st["last_contract_notice"] = today
                    cn = ledger.contract_notice(cfg, now)
                    if cn:
                        notify(cn)
                ne = ledger.news_flatten_due(st, cfg, now)
                if ne and st["position"].get("broker_symbol"):
                    st["position"]["news_flat"] = True
                    _bq.put({"op": "close", "tid": st["position"]["tid"], "kind": "NEWS"})
                    notify(M.news_flatten(ne["name"], ne["at"][11:]))
                if wd < 5 and hhmm >= cfg["summary_time_et"] and st["last_summary_date"] != today:
                    st["last_summary_date"] = today
                    notify(ledger.summary(st, cfg, now))
        except Exception as e:
            log(f"scheduler error: {type(e).__name__}: {e}")
        time.sleep(60)


def serve():
    global APP
    APP = App()
    cfg = APP.cfg()
    port = int(os.environ.get("GCP_PORT") or cfg["listen_port"])
    threading.Thread(target=_sender, daemon=True).start()
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=_broker_worker, daemon=True).start()
    srv = ThreadingHTTPServer((cfg["listen_host"], port), Handler)
    log(f"gc-paper receiver listening on {cfg['listen_host']}:{port}  home={HOME}")
    if os.environ.get("GCP_STARTUP_MSG") == "1":
        notify("🟢 GOLD paper receiver started on the Mac and is waiting for signals.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    cfg = ledger.load_config(HOME)
    if cmd == "serve":
        return serve()
    if cmd == "status":
        with locked_state(cfg) as st:
            print(M.status_text(st, cfg))
        print("broker mode:", cfg["broker"].get("mode"), "| account", cfg["broker"].get("account_id"))
        return
    if cmd == "summary":
        with locked_state(cfg) as st:
            print(ledger.summary(st, cfg, time.time()))
        return
    if cmd == "halt":
        with locked_state(cfg) as st:
            st["halted"] = " ".join(sys.argv[2:]) or "halted by hand"
        print("halted")
        return
    if cmd == "resume":
        with locked_state(cfg) as st:
            st["halted"] = ""
        print("resumed")
        return
    if cmd == "reconcile":
        px = float(sys.argv[2])
        with locked_state(cfg) as st:
            out = ledger.reconcile(st, cfg, px, time.time())
        if out["closed"]:
            ledger.append_trades(HOME, out["closed"])
        print("\n".join(out["messages"]))
        return
    raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
