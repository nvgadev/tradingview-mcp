/**
 * Public API for the ORB + VWAP + fib-pivot backtest module.
 *
 * Usage:
 *   import { runBacktest, compareVariants, generateBars, loadCsv } from './src/backtest/index.js';
 */

export { runBacktest, DEFAULT_CONFIG } from './engine.js';
export { evaluateSignal, stopDistance, DEFAULT_RULES } from './strategy.js';
export { sessionVwap, vwapSlope, atr, fibPivots, nextPivot, openingRange } from './indicators.js';
export { summarize, maxDrawdown, maxConsecutiveLosses } from './metrics.js';
export { generateBars, mulberry32, REGIMES, RANDOM_WALK } from './synthetic.js';
export { loadCsv, parseCsv, parseTimestamp } from './csv.js';
export { VARIANTS, getVariant } from './variants.js';
export { formatComparison, formatDetail, syntheticWarning } from './report.js';
export { CONTRACTS, getContract, roundToTick } from './contracts.js';
export { toEt, groupSessions, ET } from './session.js';

import { runBacktest } from './engine.js';
import { summarize, maxConsecutiveLosses } from './metrics.js';
import { getVariant, VARIANTS } from './variants.js';

/**
 * Run several variants over the same bars and return comparable results.
 *
 * @param {Array<object>} bars
 * @param {object} [opts]
 * @param {string[]} [opts.variants] variant names; defaults to all
 * @param {object} [opts.config] engine config applied to every variant
 * @returns {Array<{name:string, label:string, stats:object, trades:Array, skipped:Array,
 *                  maxConsecLosses:number}>}
 */
export function compareVariants(bars, opts = {}) {
  const names = opts.variants ?? Object.keys(VARIANTS);
  return names.map((name) => {
    const { label, rules } = getVariant(name);
    const result = runBacktest(bars, { ...(opts.config || {}), rules });
    return {
      name,
      label,
      stats: summarize(result.trades, { sessions: result.sessions }),
      trades: result.trades,
      skipped: result.skipped,
      maxConsecLosses: maxConsecutiveLosses(result.trades),
    };
  });
}
