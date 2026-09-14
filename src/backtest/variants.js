/**
 * Strategy variants for A/B comparison.
 *
 * The question this module exists to answer is not "is the combo profitable?"
 * but "does each filter earn its place?". Adding a filter that lowers
 * expectancy while also lowering trade count is strictly worse than not having
 * it, and only a side-by-side run makes that visible.
 */

import { DEFAULT_RULES } from './strategy.js';

/**
 * Named rule sets, from plainest to fully filtered.
 * @type {Record<string, {label: string, rules: object}>}
 */
export const VARIANTS = {
  orb_plain: {
    label: 'ORB only (fixed stop, 2R target)',
    rules: {
      useVwapSide: false, useVwapSlope: false, usePivotRoom: false, useAbovePivot: false,
      minScore: 1, stopMode: 'fixed', targetMode: 'rr', targetRR: 2.0,
    },
  },
  orb_atr: {
    label: 'ORB only (ATR stop, 2R target)',
    rules: {
      useVwapSide: false, useVwapSlope: false, usePivotRoom: false, useAbovePivot: false,
      minScore: 1, stopMode: 'atr', targetMode: 'rr', targetRR: 2.0,
    },
  },
  orb_vwap: {
    label: 'ORB + VWAP side & slope (ATR stop, 2R target)',
    rules: {
      useVwapSide: true, useVwapSlope: true, usePivotRoom: false, useAbovePivot: false,
      minScore: 3, stopMode: 'atr', targetMode: 'rr', targetRR: 2.0,
    },
  },
  orb_vwap_pivot: {
    label: 'ORB + VWAP + fib-pivot target & room filter',
    rules: {
      useVwapSide: true, useVwapSlope: true, usePivotRoom: true, useAbovePivot: true,
      minScore: 4, stopMode: 'atr', targetMode: 'pivot', minRR: 2.0,
    },
  },
  orb_vwap_pivot_strict: {
    label: 'Full combo, all 5 conditions required',
    rules: {
      useVwapSide: true, useVwapSlope: true, usePivotRoom: true, useAbovePivot: true,
      minScore: 5, stopMode: 'atr', targetMode: 'pivot', minRR: 2.0,
    },
  },
  full_vwap_stop: {
    label: 'Full combo with structure (VWAP) stop',
    rules: {
      useVwapSide: true, useVwapSlope: true, usePivotRoom: true, useAbovePivot: true,
      minScore: 4, stopMode: 'vwap', targetMode: 'pivot', minRR: 2.0,
    },
  },
};

/**
 * Resolve a variant name to a complete rule set.
 * @param {string} name
 * @returns {{label: string, rules: object}}
 */
export function getVariant(name) {
  const variant = VARIANTS[name];
  if (!variant) {
    throw new Error(`Unknown variant '${name}'. Known: ${Object.keys(VARIANTS).join(', ')}`);
  }
  return { label: variant.label, rules: { ...DEFAULT_RULES, ...variant.rules } };
}
