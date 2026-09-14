# ORB + VWAP + Fib-Pivot Backtest

A self-contained backtest module for the opening-range-breakout strategy
combined with a session VWAP bias filter and Fibonacci pivot targets.

**It exists to answer one question: does each filter actually earn its place?**
Not "is this strategy profitable" — that depends on your data, your market and
your execution. The module runs the same bars through a plain ORB and through
progressively filtered versions so the difference is measurable instead of
assumed.

No dependencies. Node ESM. Nothing here touches the MCP server or a live chart.

## Read this before trusting a number

- **The bundled data is synthetic.** `generateBars()` produces a seeded random
  walk with a hand-picked mix of trend and chop days. Any edge it shows is an
  artifact of the generator. It tests the *engine*, not the *market*.
- **Real conclusions need real data.** Export 5-minute bars from NinjaTrader and
  load them with `loadCsv()`. Until then, every number below is a plumbing check.
- **A backtest is the optimistic case.** It has no missed fills, no hesitation,
  no revenge trade after a loss. Live results are worse.
- **Beware filter stacking.** Each filter shrinks the sample and raises the
  chance you are fitting noise. Five conditions is already near the limit for
  the amount of data a retail trader can get.

## Running it

```bash
npm run backtest                                    # synthetic, all variants
npm run backtest -- --days 250 --seed 7 --detail
npm run backtest -- --csv data/MES_5min.csv --symbol MES --detail
npm run backtest -- --variant orb_vwap_pivot --detail
npm run backtest -- --help
npm run test:backtest
```

| Option | Meaning |
|---|---|
| `--csv PATH` | Load real bars instead of generating them |
| `--tz ET\|UTC` | How to read naive timestamps in the CSV (default `ET`) |
| `--symbol` | Contract for P&L: MES, MNQ, MYM, M2K, MGC, MCL |
| `--contracts` | Position size |
| `--days`, `--seed` | Synthetic generator controls |
| `--entry-start`, `--entry-end` | ET minutes from midnight (09:45 = 585) |
| `--max-trades` | Trades allowed per session |
| `--variant`, `--detail` | Single variant / per-variant breakdown |

## The strategy, as code

Five conditions, scored. Condition 3 is the trigger and is always required;
the other four can each be switched off, which is what makes the A/B possible.

| # | Condition | Rule (long) |
|---|---|---|
| 1 | VWAP side | close > session VWAP |
| 2 | VWAP slope | VWAP rising over the last 3 bars |
| 3 | **Trigger** | 5-min **close** above the 09:30–09:45 opening range high |
| 4 | Pivot room | distance to next fib pivot ≥ `minRR` × stop |
| 5 | Pivot side | close > daily pivot P |

Fib pivots come from the prior RTH session:

```
P  = (H + L + C) / 3
Rn = P + [0.382, 0.618, 1.000] × (H − L)
Sn = P − [0.382, 0.618, 1.000] × (H − L)
```

### Variants

| Name | What it adds |
|---|---|
| `orb_plain` | Breakout only, fixed stop, 2R target |
| `orb_atr` | Same, but ATR-sized stop |
| `orb_vwap` | + VWAP side and slope |
| `orb_vwap_pivot` | + pivot target and room filter (4 of 5 required) |
| `orb_vwap_pivot_strict` | All 5 conditions required |
| `full_vwap_stop` | Full combo with a structure (VWAP) stop |

## How the engine avoids lying to you

Three modelling choices, all deliberately pessimistic:

1. **No lookahead.** A signal is detected on a bar's *close* and filled at the
   *next* bar's *open*. You cannot trade the close of the bar that told you to
   trade. `tests/backtest.test.js` pins this down directly.
2. **Stop before target.** When one bar's range contains both levels, the stop
   is assumed to have been hit first. Without tick data the sequence is
   unknowable, so the engine takes the loss.
3. **Costs are real.** Slippage (default 1 tick, applied to entries and stop
   exits) and round-turn commission are charged on every trade. On a
   one-micro strategy these are a material share of expectancy, not noise.

The test suite's most important case runs the engine over a **driftless random
walk with zero costs** and asserts expectancy comes back near zero and the win
rate near the 33% a 2R-target system implies. A backtester that manufactures
edge out of pure noise is worse than no backtester at all, and that test is
what catches it.

### Two settings that change results more than you would expect

- **`firstBreakOnly`** (default `true`). Classic ORB: only the *first* break of
  the opening range is a signal, and when a filter rejects it the day is
  skipped. Set `false` and the strategy keeps hunting later breaks — which makes
  every filter look weaker, because a rejected signal is usually replaced by a
  later qualifying one rather than by no trade.
- **`entryEndMin` must be earlier than `flatByMin`.** Defaults are 11:00 and
  11:30. If the last entry equals the forced-flat time, a late fill is closed on
  its own entry bar and the result is a pile of meaningless near-zero trades.

## Loading real data

NinjaTrader: *Tools → Historical Data → Export*, 5-minute bars, RTH or full
session. Both common shapes parse:

```
20250303 093000;6800.25;6805.00;6798.50;6803.75;12045
time,open,high,low,close,volume
```

```js
import { loadCsv, compareVariants, formatComparison } from './src/backtest/index.js';

const bars = loadCsv('data/MES_5min.csv', { tz: 'ET' });
const results = compareVariants(bars, { config: { symbol: 'MES', contracts: 1 } });
console.log(formatComparison(results));
```

Timestamps without a zone are read as **Eastern Time** by default. Getting this
wrong silently shifts every session window and quietly invalidates the entire
run — check that `toEt(bars[0].time).minutes` is 570 for an RTH export.

## Reading the output

| Column | What it means |
|---|---|
| `Exp(R)` | **The number that decides everything.** Average R per trade. At or below zero the system loses money, whatever the win rate looks like. |
| `Win%` | Alone, meaningless. A 30% win rate at 3R beats 70% at 0.3R. |
| `PF` | Profit factor: gross win ÷ gross loss. Below 1.0 is a losing system. |
| `MaxDD(R)` | Worst peak-to-trough decline, in R. This is what sets survivable position size. |
| `Trd/Day` | Filters that cut this below ~0.3 may leave too few trades to conclude anything. |

A filter has earned its place only if it raises `Exp(R)` by more than sampling
noise. With a few hundred trades the standard error of expectancy is roughly
`1.4 / √n` R — about 0.06R at n = 600. An improvement smaller than that is not
an improvement, it is a coin flip.

## Module map

| File | Role |
|---|---|
| `session.js` | ET conversion (DST-correct) and RTH session grouping |
| `indicators.js` | VWAP, ATR, fib pivots, opening range — all causal |
| `strategy.js` | The five-condition scorecard and stop sizing |
| `engine.js` | Bar-by-bar simulation, fills, exits |
| `metrics.js` | Expectancy, profit factor, drawdown, losing streaks |
| `variants.js` | Named rule sets for A/B comparison |
| `synthetic.js` | Seeded bar generator + `RANDOM_WALK` validation fixture |
| `csv.js` | NinjaTrader / generic CSV loader |
| `report.js` | Text tables |
| `cli.js` | Runner |
