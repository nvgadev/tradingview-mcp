/**
 * Bar-by-bar backtest engine for the ORB + VWAP + fib-pivot strategy.
 *
 * Two modelling choices matter more than anything else here, and both are
 * deliberately pessimistic so the results are not flattered:
 *
 *   1. NO LOOKAHEAD. A signal is detected on a bar's CLOSE and the fill happens
 *      at the NEXT bar's OPEN. You cannot trade the close of the bar that told
 *      you to trade.
 *   2. STOP BEFORE TARGET. When a single bar's range contains both the stop and
 *      the target, the stop is assumed to have been hit first. Without tick
 *      data the true sequence is unknowable, so the engine takes the loss.
 *
 * Results are reported primarily in R multiples (profit divided by initial
 * risk) because that is account-size independent. Dollar figures are derived
 * from the contract spec and are only as good as the commission estimate.
 */

import { ET, groupSessions } from './session.js';
import { sessionVwap, atr as computeAtr, fibPivots, openingRange } from './indicators.js';
import { evaluateSignal, DEFAULT_RULES } from './strategy.js';
import { getContract, roundToTick } from './contracts.js';

export const DEFAULT_CONFIG = {
  symbol: 'MES',
  contracts: 1,

  // Session windows, in ET minutes from midnight.
  orStartMin: ET.RTH_OPEN,   // 09:30
  orEndMin: ET.OR_END,       // 09:45
  entryStartMin: ET.OR_END,  // 09:45 — raise to ET.VWAP_WARM (10:00) to let VWAP settle
  entryEndMin: ET.LAST_ENTRY, // 11:00 last entry — must be < flatByMin, or a
                              // late fill is force-closed on its own entry bar
  flatByMin: ET.MIDDAY,       // 11:30 forced flat

  /**
   * Classic ORB: only the FIRST break of the opening range is a signal. When a
   * filter rejects that break, the day is skipped rather than re-armed. Set
   * false to keep hunting later breaks — which makes filters look weaker,
   * because a rejected signal is usually replaced by a later qualifying one.
   */
  firstBreakOnly: true,

  maxTradesPerDay: 1,
  atrPeriod: 14,

  /** Slippage applied against the trade on entry and on stop exits, in ticks. */
  slippageTicks: 1,

  /**
   * Round-turn commission per contract in USD. Null uses the contract spec's
   * estimate. Set your broker's actual rate — on a 1-contract micro strategy
   * commission is a material share of expectancy, not a rounding error.
   */
  commissionRT: null,

  rules: DEFAULT_RULES,
};

/**
 * Run the backtest.
 *
 * @param {Array<{time:number,open:number,high:number,low:number,close:number,volume:number}>} bars
 *        Continuous 5-minute bars sorted ascending by time.
 * @param {object} [userConfig] merged over DEFAULT_CONFIG (rules merged separately)
 * @returns {{trades: Array<object>, sessions: number, skipped: Array<object>, config: object}}
 */
export function runBacktest(bars, userConfig = {}) {
  const config = {
    ...DEFAULT_CONFIG,
    ...userConfig,
    rules: { ...DEFAULT_RULES, ...(userConfig.rules || {}) },
  };
  const spec = getContract(config.symbol);
  const slip = config.slippageTicks * spec.tickSize;

  const atrSeries = computeAtr(bars, config.atrPeriod);
  const sessions = groupSessions(bars);

  const trades = [];
  const skipped = [];
  let priorSession = null;

  for (const session of sessions) {
    if (priorSession) {
      const dayTrades = simulateSession({
        session, priorSession, atrSeries, config, spec, slip, skipped,
      });
      trades.push(...dayTrades);
    }
    priorSession = session;
  }

  return { trades, sessions: sessions.length, skipped, config };
}

/**
 * Simulate one RTH session. Returns the trades taken that day.
 * @returns {Array<object>}
 */
function simulateSession({ session, priorSession, atrSeries, config, spec, slip, skipped }) {
  const bars = session.bars;
  const { rules } = config;

  // Prior-session high/low/close drive the pivots for today.
  let pHigh = -Infinity;
  let pLow = Infinity;
  for (const b of priorSession.bars) {
    if (b.high > pHigh) pHigh = b.high;
    if (b.low < pLow) pLow = b.low;
  }
  const pivots = fibPivots({
    high: pHigh, low: pLow, close: priorSession.bars.at(-1).close,
  });

  const or = openingRange(bars, config.orStartMin, config.orEndMin);
  if (!or) {
    skipped.push({ date: session.date, reason: 'no opening range bars' });
    return [];
  }

  const vwap = sessionVwap(bars);
  const dayTrades = [];
  let position = null;
  let pendingEntry = null;
  let breakSeen = false;

  for (let i = 0; i < bars.length; i++) {
    const bar = bars[i];

    // ── Fill a pending signal at this bar's open ────────────────────────
    if (pendingEntry && !position) {
      const dir = pendingEntry.direction;
      const entry = roundToTick(bar.open + dir * slip, spec.tickSize);
      const stop = roundToTick(entry - dir * pendingEntry.stopPoints, spec.tickSize);
      const target = roundToTick(entry + dir * pendingEntry.targetPoints, spec.tickSize);
      position = {
        ...pendingEntry,
        entryTime: bar.time,
        entryMinutes: bar.etMinutes,
        entryBar: i,
        entry, stop, target,
        riskPoints: Math.abs(entry - stop),
      };
      pendingEntry = null;
    }

    // ── Manage an open position on this bar ─────────────────────────────
    if (position) {
      const dir = position.direction;
      const hitStop = dir === 1 ? bar.low <= position.stop : bar.high >= position.stop;
      const hitTarget = dir === 1 ? bar.high >= position.target : bar.low <= position.target;

      let exit = null;
      if (hitStop) {
        // Pessimistic: stop wins any same-bar ambiguity, and it slips.
        exit = { price: roundToTick(position.stop - dir * slip, spec.tickSize), reason: 'stop' };
      } else if (hitTarget) {
        exit = { price: position.target, reason: 'target' };
      } else if (bar.etMinutes >= config.flatByMin) {
        exit = { price: roundToTick(bar.close - dir * slip, spec.tickSize), reason: 'time' };
      }

      if (exit) {
        dayTrades.push(closeTrade(position, exit, bar, config, spec));
        position = null;
        if (dayTrades.length >= config.maxTradesPerDay) return dayTrades;
      }
      continue;
    }

    // ── Look for a new signal on this bar's close ────────────────────────
    if (dayTrades.length >= config.maxTradesPerDay) continue;
    if (config.firstBreakOnly && breakSeen) continue;
    if (bar.etMinutes < config.entryStartMin || bar.etMinutes > config.entryEndMin) continue;
    if (i === bars.length - 1) continue; // nothing left to fill against

    const directions = rules.allowShort ? [1, -1] : [1];
    for (const dir of directions) {
      const broke = dir === 1 ? bar.close > or.high : bar.close < or.low;
      if (!broke) continue;
      breakSeen = true;

      const evaluation = evaluateSignal(rules, dir, {
        close: bar.close,
        vwapSeries: vwap,
        i,
        atr: atrSeries[bar.index],
        pivots,
        orHigh: or.high,
        orLow: or.low,
      });

      if (!evaluation.passed) {
        skipped.push({
          date: session.date,
          minutes: bar.etMinutes,
          direction: dir,
          score: evaluation.score,
          maxScore: evaluation.maxScore,
          conditions: evaluation.conditions,
          reason: 'score below threshold',
        });
        continue;
      }

      pendingEntry = {
        direction: dir,
        date: session.date,
        signalMinutes: bar.etMinutes,
        stopPoints: evaluation.stopPoints,
        targetPoints: evaluation.targetPoints,
        targetName: evaluation.targetName,
        score: evaluation.score,
        maxScore: evaluation.maxScore,
        orHigh: or.high,
        orLow: or.low,
        vwapAtSignal: vwap[i],
        pivots,
      };
      break;
    }
  }

  // Session ended with a position still open — exit at the last close.
  if (position) {
    const last = bars.at(-1);
    dayTrades.push(closeTrade(
      position,
      { price: roundToTick(last.close - position.direction * slip, spec.tickSize), reason: 'session_end' },
      last, config, spec,
    ));
  }

  return dayTrades;
}

/**
 * Finalise a trade record with R multiple and dollar P&L.
 * @returns {object}
 */
function closeTrade(position, exit, bar, config, spec) {
  const dir = position.direction;
  const pointsPnl = (exit.price - position.entry) * dir;
  const r = position.riskPoints > 0 ? pointsPnl / position.riskPoints : 0;
  const gross = pointsPnl * spec.pointValue * config.contracts;
  const commission = (config.commissionRT ?? spec.commissionRT) * config.contracts;

  return {
    date: position.date,
    direction: dir === 1 ? 'long' : 'short',
    signalMinutes: position.signalMinutes,
    entryMinutes: position.entryMinutes,
    exitMinutes: bar.etMinutes,
    entry: position.entry,
    stop: position.stop,
    target: position.target,
    exit: exit.price,
    exitReason: exit.reason,
    riskPoints: position.riskPoints,
    pointsPnl,
    r,
    grossUsd: gross,
    commissionUsd: commission,
    netUsd: gross - commission,
    score: position.score,
    maxScore: position.maxScore,
    targetName: position.targetName,
  };
}
