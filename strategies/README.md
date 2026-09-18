# Regime-Filtered Momentum Pullback — US equities

A daily-timeframe swing system for US large caps. Three stacked edges: a market
regime filter, relative-strength momentum, and a pullback entry that keeps the
initial stop tight so winners run at a high R multiple.

`momentum_pullback.pine` is the backtestable single-symbol version. The rules
below that Pine cannot see on one chart — the portfolio overlay and the earnings
blackout — are enforced by the screener that feeds it.

## Rules

| Stage | Rule |
|---|---|
| Regime | SPY close > 200 SMA, else no new longs |
| Universe | Market cap > $10B, price > $15, 30-day avg volume > 2M shares |
| Trend | Close > 50 EMA > 200 EMA |
| Momentum | Stock 126-bar return > SPY 126-bar return |
| Pullback | RSI(3) < 25 **or** low touches the 20 EMA |
| Entry | Buy-stop one tick above the setup bar's high, good for one bar |
| Initial stop | Entry − 2 × ATR(14) |
| Trail | Chandelier: (highest high since entry) − 3 × ATR(14), ratcheted |
| Time stop | 20 bars with price below entry |
| Size | 1% of equity at risk, capped at 20% of equity notional |

### Portfolio overlay (not in the Pine script)

- Max 8 concurrent positions.
- Max 2 positions per sector, max 25% of equity in one sector.
- No entry within 7 trading days of an earnings report; close before the report
  if already in the position. Pine has no forward earnings date, so this filter
  lives in the screener.

### Why the position cap exists

Risk-based sizing alone is unstable across volatility regimes. A stock whose ATR
is 1.4% of its price sizes to roughly 36% of equity at 1% risk — technically
within the risk budget, but a single gap through the stop then costs far more
than 1%. The 20% notional cap binds on quiet names and is inert on volatile
ones, which is the intended asymmetry.

## Expected results

Realistic targets after costs, over 150+ trades spanning a full cycle: win rate
50–55%, average win / average loss around 2.0, profit factor 1.8–2.3, max
drawdown 15–20%, 50–70 trades per year.

Treat a backtest showing a profit factor above 3 as a bug, not a discovery —
check for survivorship bias, a too-short sample, or parameters tuned on the same
data they are measured on.

## Costs modelled

`commission_value = 0.01` (percent per side) plus 2 ticks of slippage,
approximating a $0-commission US broker where the real cost is spread and
impact. Tighten slippage for mega caps, loosen it below $25M average daily
dollar volume.

## Running the backtest in TradingView

The MCP tools in this repo drive a local TradingView Desktop over CDP, so these
steps run from a Claude Code session on the machine where TradingView Desktop is
running with `--remote-debugging-port=9222`. They do not work from a cloud
session.

1. `tv_health_check` — confirm CDP is reachable.
2. `chart_set_symbol` with `"AMEX:SPY"`, then `chart_set_timeframe` with `"D"`.
3. `ui_open_panel` with `"pine-editor"`.
4. `pine_new` with type `"strategy"`.
5. `pine_set_source` with the contents of `momentum_pullback.pine`.
6. `pine_smart_compile`, then `pine_get_errors` to confirm a clean compile.
7. `pine_save` with a name, e.g. `"Momentum Pullback"`.
8. `ui_open_panel` with `"strategy-tester"` and read the Performance Summary.
9. `chart_set_symbol` for each test ticker, re-reading the tester each time.
10. `capture_screenshot` with region `"strategy_tester"` to keep the numbers.

Backtest one symbol at a time: TradingView's Strategy Tester is single-symbol,
so portfolio-level results have to be aggregated across runs. Weight each
symbol's results equally rather than summing raw net profit, which would let the
highest-priced ticker dominate.

## Validation before risking money

- 150+ trades minimum, spanning at least one bear phase.
- Walk-forward: tune on the first ~60% of the sample, measure on the untouched
  remainder. An out-of-sample profit factor more than ~30% below in-sample means
  the parameters are fitted, not found.
- Monte Carlo: shuffle trade order over ~1000 runs and read the worst-case
  drawdown, not the average one.
- Paper trade a full quarter before sizing up.

## Screener

The live screener that produces candidates for this system is saved on Robinhood
as *Momentum Pullback (Regime-Filtered) — Claude*. It encodes the universe,
trend and pullback filters. Relative strength, the earnings blackout and the
sector caps are applied to its output by hand — the scanner's filter language
covers the first set but not the second.
