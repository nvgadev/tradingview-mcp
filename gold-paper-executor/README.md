# Gold paper-trading system (GOPI HEDGE X PRO -> Webull paper)

**Status: STOPPED on 2026-10-23** (launchd jobs removed, TradingView alerts deleted). Code kept here so it can be resumed.

```
TradingView alert (Pine alert(), JSON)  ->  ngrok tunnel  ->  receiver.py (Mac)  ->  Webull sandbox orders
                                                                      |-> Telegram (plain-English messages)
```

## What the strategy was
* Indicator: `pine/gopi-hedge-x-pro.pine` (Chandelier trend flips, pullback-and-resume entries, confidence score).
* Profile that was deployed: gold (MGC1!) **15m**, entries only when the **4-hour trend agrees**
  (`useHtfFilter`), windows 07:00-16:30 and 18:00-21:30 New York time, trade closed at the end of each window,
  no trend-flip exit. Stop 1.5 ATR; TP1 1R (stop -> +0.2R), TP2 2R (stop -> TP1), TP3 3R closes. All in / all out.
* Executed on Webull *sandbox* as 6 x 1-Ounce Gold (1OZ) with a resting stop order managed by the program.
* Guards: daily loss cap 3 %, halt at -15 %, news calendar (`events.json`: no new trades around CPI/jobs/PPI/GDP/PCE/Fed,
  open trade closed 5 min before), stale-signal skip, duplicate-event skip, sandbox-only + verified-call-shape guards.

## Research findings (all backtests in TradingView, 1 micro contract, costs included)
* 5m version: no edge (P(+1R before -1R) = 47 %). 40+ stop/target/trail combinations all <= 0.
* 4h swing version on gold, 7 years: +$14k, stable across neighbouring settings, but long-biased (gold bull run).
* 15m + 4h filter, 0.9 years, intraday only: +$5.3k (239 trades, max drop -$1.9k); adding the 18:00-21:30 window: +$6.1k, drop -$2.7k.
* ES / NQ / MES / MNQ with the same setup: negative or inconsistent. Gold only.
* Evidence is short (0.9 years); a 3-month paper test (~100 trades) was the plan before trusting it.

## Live paper result (2026-10-20 .. 2026-10-23)
Only a handful of signals. **Known problems found when stopping:**
1. Webull history shows both trades the program executed (2026-10-20) closed by a market order ~2 s after entry -
   consistent with the program's "stop order refused -> close immediately" safety path. The refusal reason was not logged. Fix before resuming:
   log the Webull error text and test the STOP_LOSS call on an open short position.
2. The Mac slept (battery) and one entry signal (2026-10-16 07:00 ET) was lost; the Mac later ran out of battery.
   The receiver must run on an always-on machine (or the Mac must stay plugged in, lid open).

## Files
* `receiver.py` webhook server + scheduler + CLI (`status`, `halt`, `resume`, `reconcile PRICE`)
* `ledger.py` pure rules (entries, targets, exits, rails, news guard, contract roll); `messages.py` Telegram text
* `broker.py` + `webull_lite.py` Webull sandbox adapter (stdlib only); `broker_check.py` one-time order-call check
* `compare.py` compares day policies (every trade / first trade of day / stop after loss / 3-part exit) from `trades.csv`
* `tests/` 45 unit tests (`python3 -m unittest discover -s tests`)
* `config.example.json` copy to `config.json`; set your own sandbox futures account id

## To resume (not done automatically)
1. Copy this folder to `~/Library/Application Support/gopi-gc-paper/`, create `secret.env` (`WEBHOOK_SECRET=<random 24+ chars>`, chmod 600)
   and `~/.config/webull/openapi.env` (`WEBULL_APP_KEY`, `WEBULL_APP_SECRET`; sandbox).
2. Deploy the indicator with the same secret baked in (`scripts/gopi_variant.py LABEL webhookSecret=@secret`), then create ONE
   TradingView alert on it ("Any alert() function call", webhook URL = your tunnel + `/hook`). Alerts freeze the script version.
3. Run `broker_check.py --yes` once, then set `broker.mode` to `paper`; start `receiver.py serve` under launchd and a tunnel.
4. Never point it at a live Webull account: the code refuses anything but `api.sandbox.webull.com`.

`research/` holds the offline study scripts (`make_research_copy.py` logs per-trade price paths into hidden labels,
`analyze.py` / `features.py` evaluate exits and entry filters). Market data files are intentionally not committed.
