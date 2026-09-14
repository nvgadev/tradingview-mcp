/**
 * ORB + VWAP + Fibonacci-pivot entry rules.
 *
 * The strategy is expressed as a five-condition scorecard so that each filter
 * can be switched off independently. That is the whole point of this module:
 * it lets the backtest answer "does adding VWAP actually help?" rather than
 * assuming it does.
 *
 *   1. price on the correct side of VWAP
 *   2. VWAP sloping in the trade's direction
 *   3. 5-min close beyond the opening range (the trigger — always required)
 *   4. room to the next fib pivot >= minRR x stop distance
 *   5. price on the correct side of the daily pivot P
 */

import { vwapSlope, nextPivot } from './indicators.js';

/** @typedef {1|-1} Direction 1 = long, -1 = short */

export const DEFAULT_RULES = {
  // Which conditions participate in the score. Condition 3 is structural and
  // is always required — a signal without a breakout is not an ORB signal.
  useVwapSide: true,
  useVwapSlope: true,
  usePivotRoom: true,
  useAbovePivot: true,

  /** Minimum score (out of the enabled conditions + 1 trigger) to take a trade. */
  minScore: 4,

  /** Bars of lookback for the VWAP slope test. */
  slopeLookback: 3,

  /** Required reward:risk before a trade is allowed (condition 4). */
  minRR: 2.0,

  /** Stop sizing: 'fixed' | 'atr' | 'vwap'. */
  stopMode: 'atr',
  fixedStopPoints: 8,
  atrMult: 1.2,
  vwapBufferPoints: 2,
  minStopPoints: 3,
  maxStopPoints: 20,

  /** Target sizing: 'rr' (multiple of stop) | 'pivot' (next fib level). */
  targetMode: 'pivot',
  targetRR: 2.0,
  /** Exit this far in front of the pivot — resting orders cluster at the level. */
  pivotBufferPoints: 2,

  /** Allow shorts as well as longs. */
  allowShort: true,
};

/**
 * Size the stop in points for a candidate entry.
 *
 * @param {object} rules
 * @param {Direction} direction
 * @param {number} entryPrice
 * @param {{vwap:number, atr:number|null, orHigh:number, orLow:number}} ctx
 * @returns {number|null} stop distance in points, or null when it cannot be sized
 */
export function stopDistance(rules, direction, entryPrice, ctx) {
  let dist;
  switch (rules.stopMode) {
    case 'fixed':
      dist = rules.fixedStopPoints;
      break;
    case 'atr':
      if (ctx.atr == null) return null;
      dist = ctx.atr * rules.atrMult;
      break;
    case 'vwap': {
      // Structure stop: beyond VWAP, but never inside the opening range edge
      // we just broke, and never wider than maxStopPoints.
      const vwapDist = direction === 1
        ? entryPrice - (ctx.vwap - rules.vwapBufferPoints)
        : (ctx.vwap + rules.vwapBufferPoints) - entryPrice;
      const orDist = direction === 1
        ? entryPrice - ctx.orLow
        : ctx.orHigh - entryPrice;
      dist = Math.min(
        vwapDist > 0 ? vwapDist : Infinity,
        orDist > 0 ? orDist : Infinity,
      );
      if (!Number.isFinite(dist)) return null;
      break;
    }
    default:
      throw new Error(`Unknown stopMode '${rules.stopMode}'`);
  }

  if (!Number.isFinite(dist) || dist <= 0) return null;
  return Math.min(Math.max(dist, rules.minStopPoints), rules.maxStopPoints);
}

/**
 * Evaluate the scorecard for one direction at one bar.
 *
 * The caller has already established that this bar closed beyond the opening
 * range in `direction` (the trigger).
 *
 * @param {object} rules
 * @param {Direction} direction
 * @param {object} ctx
 * @param {number} ctx.close close of the signal bar
 * @param {number[]} ctx.vwapSeries
 * @param {number} ctx.i index of the signal bar within the session
 * @param {number|null} ctx.atr
 * @param {object} ctx.pivots
 * @param {number} ctx.orHigh
 * @param {number} ctx.orLow
 * @returns {{score:number, maxScore:number, passed:boolean, conditions:object,
 *            stopPoints:number|null, targetPoints:number|null, targetName:string|null}}
 */
export function evaluateSignal(rules, direction, ctx) {
  const { close, vwapSeries, i, pivots } = ctx;
  const vwap = vwapSeries[i];
  const conditions = { trigger: true };
  let maxScore = 1; // the trigger itself

  if (rules.useVwapSide) {
    maxScore++;
    conditions.vwapSide = direction === 1 ? close > vwap : close < vwap;
  }
  if (rules.useVwapSlope) {
    maxScore++;
    const slope = vwapSlope(vwapSeries, i, rules.slopeLookback);
    conditions.vwapSlope = direction === 1 ? slope > 0 : slope < 0;
  }
  if (rules.useAbovePivot) {
    maxScore++;
    conditions.pivotSide = direction === 1 ? close > pivots.p : close < pivots.p;
  }

  // Stop must be sizeable before room can be judged against it.
  const stopPoints = stopDistance(rules, direction, close, {
    vwap, atr: ctx.atr, orHigh: ctx.orHigh, orLow: ctx.orLow,
  });

  let targetPoints = null;
  let targetName = null;
  if (rules.targetMode === 'pivot') {
    const target = nextPivot(pivots, close, direction);
    if (target) {
      const raw = Math.abs(target.price - close) - rules.pivotBufferPoints;
      if (raw > 0) {
        targetPoints = raw;
        targetName = target.name;
      }
    }
  } else if (stopPoints != null) {
    targetPoints = stopPoints * rules.targetRR;
    targetName = `${rules.targetRR}R`;
  }

  if (rules.usePivotRoom) {
    maxScore++;
    conditions.pivotRoom = stopPoints != null
      && targetPoints != null
      && targetPoints / stopPoints >= rules.minRR;
  }

  const score = Object.values(conditions).filter(Boolean).length;
  const tradeable = stopPoints != null && targetPoints != null && targetPoints > 0;

  return {
    score,
    maxScore,
    conditions,
    stopPoints,
    targetPoints,
    targetName,
    passed: tradeable && score >= Math.min(rules.minScore, maxScore),
  };
}
