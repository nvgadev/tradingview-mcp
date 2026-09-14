# Pine Scripts

## `orb_vwap_pivot.pine` — ORB + VWAP + Fibonacci Pivots

A TradingView **strategy** (not an indicator), so the Strategy Tester reports on
it directly. It is the same rule set as the JS backtest in `src/backtest/`, so
the two can be cross-checked against each other.

### Setting it up

With TradingView Desktop running on CDP port 9222:

```bash
npm run tv -- launch                    # if it is not already running
node scripts/setup_orb_layout.js        # creates the layout and loads the strategy
```

That creates a chart layout named **ORB VWAP FIB**, sets `CME_MINI:MES1!` on a
5-minute chart, opens a new Pine strategy, injects this file, compiles it,
opens the Strategy Tester and screenshots the result. Options:

```bash
node scripts/setup_orb_layout.js --layout "ORB Lab" --symbol "CME_MINI:MNQ1!" --tf 5
node scripts/setup_orb_layout.js --save     # also save the script to your TV cloud
```

To do it by hand instead: open the Pine editor, paste this file, press
**Add to chart**, then open the Strategy Tester tab.

### The rules

Five conditions, scored. Condition 3 is the trigger and is always required;
each of the other four is an input checkbox, which is the whole point — you can
turn them off one at a time and watch what happens to the Performance Summary.

| # | Condition | Long |
|---|---|---|
| 1 | VWAP side | close > session VWAP |
| 2 | VWAP slope | VWAP rising over the last 3 bars |
| 3 | **Trigger** | bar **close** above the 09:30–09:45 opening range high |
| 4 | Pivot room | distance to next fib pivot ≥ `minRR` × stop |
| 5 | Pivot side | close > daily pivot P |

Pivots come from the prior daily bar via `request.security(..., lookahead_on)`
with a `[1]` offset — the non-repainting idiom, since the previous day's
high/low/close are already fixed while today is trading.

### Defaults

| Input | Default | Why |
|---|---|---|
| Opening range | 09:30–09:45 | 3 bars on a 5-minute chart |
| First / last entry | 09:45 / 11:00 | |
| Force flat | 11:30 | Must be **later** than last entry, or a late fill is closed on its own bar |
| Min score | 4 of 5 | One condition may fail |
| Stop | ATR(14) × 1.2 | Adapts to the day's volatility; a fixed stop does not |
| Target | Next fib pivot − 2 pts | Exit in front of the level, where resting orders sit |
| First break only | on | Classic ORB — if the first break fails a filter, the day is skipped |
| Slippage / commission | 1 tick / $0.62 per side | Change these to your broker's actual rates |

### Reading the Strategy Tester honestly

- **Total Closed Trades** first. Under ~100 the numbers are noise, whatever they say.
- **Profit Factor** and **Max Drawdown**, not Net Profit. Net Profit alone hides
  how much pain produced it.
- A filter has earned its place only if it improves results by more than
  sampling noise — roughly `1.4/√n` R. With 100 trades that is ~0.14R.
- Turn the four filter checkboxes off one at a time and compare. If a filter
  cuts the trade count without improving expectancy, it is costing you.

### What has and has not been verified

This file passes the repo's offline static analysis
(`npm run tv -- pine analyze --file pine/orb_vwap_pivot.pine`), and its logic
mirrors the JS engine in `src/backtest/`, which has 66 passing tests including a
no-lookahead check.

It has **not** been compiled by TradingView's Pine compiler — that needs either
a live TradingView session or network access to `pine-facade.tradingview.com`.
The first `Add to chart` is the real compile; `scripts/setup_orb_layout.js`
reads the errors back and prints them if any appear.

Backtest results from the Strategy Tester are also the optimistic case: no
missed fills, no hesitation, no revenge trade after a loss.
