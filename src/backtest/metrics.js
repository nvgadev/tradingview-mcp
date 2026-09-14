/**
 * Performance metrics for a list of trades.
 *
 * R multiples are the primary unit: 1R is the risk taken on entry, so a set of
 * trades can be compared across contracts and account sizes. Expectancy in R
 * is the single number that decides whether a strategy is worth trading —
 * anything at or below zero is a losing system no matter how good the win rate
 * looks.
 */

/**
 * @param {Array<object>} trades from runBacktest()
 * @param {{sessions?: number}} [context]
 * @returns {object} summary statistics
 */
export function summarize(trades, context = {}) {
  const n = trades.length;
  if (n === 0) {
    return {
      trades: 0, wins: 0, losses: 0, winRate: 0,
      expectancyR: 0, totalR: 0, avgWinR: 0, avgLossR: 0,
      profitFactor: 0, maxDrawdownR: 0,
      netUsd: 0, grossUsd: 0, commissionUsd: 0, maxDrawdownUsd: 0,
      sessions: context.sessions ?? 0, tradesPerSession: 0,
      byExitReason: {}, byDirection: {},
    };
  }

  const wins = trades.filter((t) => t.r > 0);
  const losses = trades.filter((t) => t.r <= 0);
  const sum = (arr, key) => arr.reduce((a, t) => a + t[key], 0);

  const totalR = sum(trades, 'r');
  const grossWinR = sum(wins, 'r');
  const grossLossR = Math.abs(sum(losses, 'r'));

  const byExitReason = {};
  for (const t of trades) {
    byExitReason[t.exitReason] = (byExitReason[t.exitReason] || 0) + 1;
  }

  const byDirection = {};
  for (const dir of ['long', 'short']) {
    const subset = trades.filter((t) => t.direction === dir);
    if (subset.length === 0) continue;
    byDirection[dir] = {
      trades: subset.length,
      winRate: subset.filter((t) => t.r > 0).length / subset.length,
      expectancyR: sum(subset, 'r') / subset.length,
    };
  }

  return {
    trades: n,
    wins: wins.length,
    losses: losses.length,
    winRate: wins.length / n,
    expectancyR: totalR / n,
    totalR,
    avgWinR: wins.length ? grossWinR / wins.length : 0,
    avgLossR: losses.length ? -grossLossR / losses.length : 0,
    profitFactor: grossLossR > 0 ? grossWinR / grossLossR : (grossWinR > 0 ? Infinity : 0),
    maxDrawdownR: maxDrawdown(trades.map((t) => t.r)),
    grossUsd: sum(trades, 'grossUsd'),
    commissionUsd: sum(trades, 'commissionUsd'),
    netUsd: sum(trades, 'netUsd'),
    maxDrawdownUsd: maxDrawdown(trades.map((t) => t.netUsd)),
    sessions: context.sessions ?? 0,
    tradesPerSession: context.sessions ? n / context.sessions : 0,
    byExitReason,
    byDirection,
  };
}

/**
 * Peak-to-trough drawdown of a cumulative series built from per-trade results.
 * @param {number[]} increments
 * @returns {number} positive magnitude of the worst drawdown
 */
export function maxDrawdown(increments) {
  let equity = 0;
  let peak = 0;
  let worst = 0;
  for (const inc of increments) {
    equity += inc;
    if (equity > peak) peak = equity;
    const dd = peak - equity;
    if (dd > worst) worst = dd;
  }
  return worst;
}

/**
 * Longest run of consecutive losing trades — the number that actually decides
 * whether a position size is survivable.
 * @param {Array<{r:number}>} trades
 * @returns {number}
 */
export function maxConsecutiveLosses(trades) {
  let run = 0;
  let worst = 0;
  for (const t of trades) {
    if (t.r <= 0) {
      run++;
      if (run > worst) worst = run;
    } else {
      run = 0;
    }
  }
  return worst;
}
