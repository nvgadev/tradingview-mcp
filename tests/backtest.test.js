/**
 * Tests for the ORB + VWAP + fib-pivot backtest module (src/backtest/).
 *
 * The engine tests are the ones that matter: they pin down no-lookahead entry,
 * pessimistic same-bar stop resolution, the forced-flat rule, and — most
 * importantly — that the engine reports ~zero expectancy on a driftless random
 * walk. A backtester that manufactures edge out of noise is worse than useless.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { toEt, groupSessions, ET } from '../src/backtest/session.js';
import {
  sessionVwap, vwapSlope, atr, fibPivots, nextPivot, openingRange,
} from '../src/backtest/indicators.js';
import { evaluateSignal, stopDistance, DEFAULT_RULES } from '../src/backtest/strategy.js';
import { runBacktest } from '../src/backtest/engine.js';
import { summarize, maxDrawdown, maxConsecutiveLosses } from '../src/backtest/metrics.js';
import { generateBars, mulberry32, RANDOM_WALK } from '../src/backtest/synthetic.js';
import { parseCsv, parseTimestamp } from '../src/backtest/csv.js';
import { getContract, roundToTick } from '../src/backtest/contracts.js';
import { getVariant, VARIANTS } from '../src/backtest/variants.js';

// ── Fixture helpers ──────────────────────────────────────────────────────

/** Epoch ms for an ET wall-clock time on a given date. */
function etMs(date, minutes) {
  const hh = String(Math.floor(minutes / 60)).padStart(2, '0');
  const mm = String(minutes % 60).padStart(2, '0');
  return parseTimestamp(`${date} ${hh}:${mm}`, 'ET');
}

function bar(date, minutes, o, h, l, c, v = 1000) {
  return { time: etMs(date, minutes), open: o, high: h, low: l, close: c, volume: v };
}

/** A flat prior session that yields predictable pivots. */
function priorSession(date, high, low, close) {
  const mid = (high + low) / 2;
  return [
    bar(date, 570, mid, high, low, mid),
    bar(date, 575, mid, high, low, mid),
    bar(date, 580, mid, high, low, close),
  ];
}

/** Rules with every optional filter disabled — only the breakout trigger. */
const TRIGGER_ONLY = {
  useVwapSide: false, useVwapSlope: false, usePivotRoom: false, useAbovePivot: false,
  minScore: 1, stopMode: 'fixed', fixedStopPoints: 8, targetMode: 'rr', targetRR: 2.0,
  maxStopPoints: 100,
};

// ── session.js ───────────────────────────────────────────────────────────

describe('toEt()', () => {
  it('maps 14:30 UTC to 09:30 ET during standard time', () => {
    assert.deepEqual(toEt(Date.UTC(2025, 1, 3, 14, 30)), { date: '2025-02-03', minutes: 570 });
  });

  it('maps 13:30 UTC to 09:30 ET during daylight time', () => {
    assert.deepEqual(toEt(Date.UTC(2025, 5, 3, 13, 30)), { date: '2025-06-03', minutes: 570 });
  });

  it('handles midnight ET without emitting hour 24', () => {
    assert.equal(toEt(Date.UTC(2025, 5, 3, 4, 0)).minutes, 0);
  });
});

describe('groupSessions()', () => {
  it('keeps only RTH bars and groups them by ET date', () => {
    const bars = [
      bar('2025-03-03', 480, 1, 1, 1, 1),  // 08:00 pre-market — dropped
      bar('2025-03-03', 570, 1, 1, 1, 1),
      bar('2025-03-03', 955, 1, 1, 1, 1),
      bar('2025-03-03', 960, 1, 1, 1, 1),  // 16:00 close — exclusive, dropped
      bar('2025-03-04', 600, 1, 1, 1, 1),
    ];
    const sessions = groupSessions(bars);
    assert.equal(sessions.length, 2);
    assert.equal(sessions[0].date, '2025-03-03');
    assert.equal(sessions[0].bars.length, 2);
    assert.equal(sessions[1].bars.length, 1);
  });

  it('annotates bars with etMinutes and the source index', () => {
    const bars = [bar('2025-03-03', 570, 1, 1, 1, 1), bar('2025-03-03', 575, 1, 1, 1, 1)];
    const [session] = groupSessions(bars);
    assert.equal(session.bars[1].etMinutes, 575);
    assert.equal(session.bars[1].index, 1);
  });
});

// ── indicators.js ────────────────────────────────────────────────────────

describe('sessionVwap()', () => {
  it('equals the typical price on the first bar', () => {
    const bars = [{ high: 12, low: 6, close: 9, volume: 100 }];
    assert.equal(sessionVwap(bars)[0], 9);
  });

  it('weights by volume', () => {
    const bars = [
      { high: 10, low: 10, close: 10, volume: 1 },
      { high: 20, low: 20, close: 20, volume: 3 },
    ];
    // (10*1 + 20*3) / 4 = 17.5
    assert.equal(sessionVwap(bars)[1], 17.5);
  });

  it('treats zero-volume bars as weight 1 rather than dividing by zero', () => {
    const out = sessionVwap([{ high: 10, low: 10, close: 10, volume: 0 }]);
    assert.equal(out[0], 10);
  });
});

describe('vwapSlope()', () => {
  it('is positive for a rising VWAP and zero without enough history', () => {
    const series = [10, 11, 12, 13];
    assert.ok(vwapSlope(series, 3, 3) > 0);
    assert.equal(vwapSlope(series, 1, 3), 0);
  });

  it('is negative for a falling VWAP', () => {
    assert.ok(vwapSlope([13, 12, 11, 10], 3, 3) < 0);
  });
});

describe('atr()', () => {
  it('returns null until the period is filled', () => {
    const bars = Array.from({ length: 5 }, () => ({ high: 2, low: 1, close: 1.5 }));
    const out = atr(bars, 3);
    assert.equal(out[1], null);
    assert.equal(out[2], 1);
  });

  it('averages a constant true range to that range', () => {
    const bars = Array.from({ length: 30 }, () => ({ high: 11, low: 10, close: 10.5 }));
    const out = atr(bars, 14);
    assert.ok(Math.abs(out.at(-1) - 1) < 1e-9);
  });
});

describe('fibPivots()', () => {
  it('computes P and the fib extensions from the prior range', () => {
    const p = fibPivots({ high: 110, low: 100, close: 105 });
    assert.equal(p.p, 105);
    assert.equal(p.range, 10);
    assert.ok(Math.abs(p.r1 - 108.82) < 1e-9);
    assert.ok(Math.abs(p.r2 - 111.18) < 1e-9);
    assert.equal(p.r3, 115);
    assert.ok(Math.abs(p.s1 - 101.18) < 1e-9);
    assert.equal(p.s3, 95);
  });
});

describe('nextPivot()', () => {
  const pivots = fibPivots({ high: 110, low: 100, close: 105 });

  it('finds the nearest level above for a long', () => {
    assert.equal(nextPivot(pivots, 106, 1).name, 'r1');
  });

  it('finds the nearest level below for a short', () => {
    assert.equal(nextPivot(pivots, 104, -1).name, 's1');
  });

  it('returns null beyond the outermost level', () => {
    assert.equal(nextPivot(pivots, 999, 1), null);
  });
});

describe('openingRange()', () => {
  it('spans only bars inside the window', () => {
    const bars = [
      { etMinutes: 570, high: 102, low: 98 },
      { etMinutes: 575, high: 101, low: 97 },
      { etMinutes: 585, high: 120, low: 80 }, // outside — must not widen the range
    ];
    assert.deepEqual(openingRange(bars, 570, 585), { high: 102, low: 97, bars: 2 });
  });

  it('returns null when no bars fall in the window', () => {
    assert.equal(openingRange([{ etMinutes: 700, high: 1, low: 1 }], 570, 585), null);
  });
});

// ── contracts.js ─────────────────────────────────────────────────────────

describe('contracts', () => {
  it('rounds prices to the contract tick grid', () => {
    assert.equal(roundToTick(6800.31, 0.25), 6800.25);
    assert.equal(roundToTick(6800.4, 0.25), 6800.5);
  });

  it('exposes MES at $5 per point', () => {
    assert.equal(getContract('mes').pointValue, 5);
  });

  it('throws on an unknown symbol', () => {
    assert.throws(() => getContract('NOPE'), /Unknown contract/);
  });
});

// ── strategy.js ──────────────────────────────────────────────────────────

describe('stopDistance()', () => {
  const ctx = { vwap: 100, atr: 5, orHigh: 104, orLow: 96 };

  it('uses the fixed distance in fixed mode', () => {
    assert.equal(stopDistance({ ...DEFAULT_RULES, stopMode: 'fixed', fixedStopPoints: 7 }, 1, 105, ctx), 7);
  });

  it('scales ATR by the multiplier', () => {
    assert.equal(stopDistance({ ...DEFAULT_RULES, stopMode: 'atr', atrMult: 1.2 }, 1, 105, ctx), 6);
  });

  it('returns null in ATR mode when ATR is unavailable', () => {
    const rules = { ...DEFAULT_RULES, stopMode: 'atr' };
    assert.equal(stopDistance(rules, 1, 105, { ...ctx, atr: null }), null);
  });

  it('takes the tighter of VWAP and opening-range structure', () => {
    // long at 105: VWAP stop = 105 - (100-2) = 7; OR low stop = 105 - 96 = 9
    const rules = { ...DEFAULT_RULES, stopMode: 'vwap', vwapBufferPoints: 2 };
    assert.equal(stopDistance(rules, 1, 105, ctx), 7);
  });

  it('clamps to the configured min and max', () => {
    const rules = { ...DEFAULT_RULES, stopMode: 'fixed', fixedStopPoints: 99, maxStopPoints: 20 };
    assert.equal(stopDistance(rules, 1, 105, ctx), 20);
    const tight = { ...DEFAULT_RULES, stopMode: 'fixed', fixedStopPoints: 0.5, minStopPoints: 3 };
    assert.equal(stopDistance(tight, 1, 105, ctx), 3);
  });
});

describe('evaluateSignal()', () => {
  // P = 125, r1 = 144.1. Price at 130 is above VWAP (129), above P, with a
  // rising VWAP and 12.1 points of room to r1 after the 2-point buffer.
  const pivots = fibPivots({ high: 150, low: 100, close: 125 });
  const base = {
    close: 130, vwapSeries: [126, 127, 128, 129], i: 3,
    atr: 5, pivots, orHigh: 129.5, orLow: 120,
  };

  it('scores every enabled condition when all align for a long', () => {
    const rules = { ...DEFAULT_RULES, stopMode: 'fixed', fixedStopPoints: 5, minRR: 2 };
    const out = evaluateSignal(rules, 1, base);
    assert.equal(out.maxScore, 5);
    assert.equal(out.score, 5);        // room is 12.1 / 5 = 2.42R
    assert.equal(out.passed, true);
  });

  it('drops conditions from maxScore when their filter is disabled', () => {
    const rules = { ...DEFAULT_RULES, ...TRIGGER_ONLY };
    const out = evaluateSignal(rules, 1, base);
    assert.equal(out.maxScore, 1);
    assert.deepEqual(Object.keys(out.conditions), ['trigger']);
  });

  it('fails the room condition when the pivot is nearer than minRR x stop', () => {
    // Room to r1 is 12.1 points; against a 10-point stop that is only 1.21R.
    const rules = {
      ...DEFAULT_RULES, stopMode: 'fixed', fixedStopPoints: 10,
      minRR: 2, targetMode: 'pivot', minScore: 5,
    };
    const out = evaluateSignal(rules, 1, base);
    assert.equal(out.conditions.pivotRoom, false);
    assert.equal(out.passed, false);
  });

  it('tolerates one failed condition when minScore is 4 of 5', () => {
    // Same rejected room condition, but the 4-of-5 threshold still lets it through.
    const rules = {
      ...DEFAULT_RULES, stopMode: 'fixed', fixedStopPoints: 10,
      minRR: 2, targetMode: 'pivot', minScore: 4,
    };
    const out = evaluateSignal(rules, 1, base);
    assert.equal(out.conditions.pivotRoom, false);
    assert.equal(out.score, 4);
    assert.equal(out.passed, true);
  });

  it('marks the VWAP side false when price is on the wrong side', () => {
    const out = evaluateSignal({ ...DEFAULT_RULES, stopMode: 'fixed' }, -1, base);
    assert.equal(out.conditions.vwapSide, false); // short with close above VWAP
  });

  it('is not tradeable when the stop cannot be sized', () => {
    const rules = { ...DEFAULT_RULES, stopMode: 'atr' };
    const out = evaluateSignal(rules, 1, { ...base, atr: null });
    assert.equal(out.stopPoints, null);
    assert.equal(out.passed, false);
  });
});

// ── engine.js — the rules that decide whether results are trustworthy ────

describe('runBacktest() — fill mechanics', () => {
  const D0 = '2025-03-03';
  const D1 = '2025-03-04';

  /** OR is 98–102; the 09:45 bar closes at 103 (a break); 09:50 opens at 105. */
  function breakoutDay(extraBars = []) {
    return [
      ...priorSession(D0, 92, 88, 90),
      bar(D1, 570, 100, 102, 98, 100),
      bar(D1, 575, 100, 101, 99, 100),
      bar(D1, 580, 100, 102, 98, 100),
      bar(D1, 585, 100, 103, 99, 103),   // signal bar (close > OR high)
      ...extraBars,
    ];
  }

  it('fills at the NEXT bar open, never at the signal bar close', () => {
    const bars = breakoutDay([
      bar(D1, 590, 105, 106, 104, 105),
      bar(D1, 595, 105, 106, 104, 105),
    ]);
    const { trades } = runBacktest(bars, {
      slippageTicks: 0, rules: TRIGGER_ONLY,
    });
    assert.equal(trades.length, 1);
    assert.equal(trades[0].entry, 105, 'entry must be the 09:50 open, not the 09:45 close of 103');
    assert.equal(trades[0].signalMinutes, 585);
    assert.equal(trades[0].entryMinutes, 590);
  });

  it('applies slippage against the trade on entry', () => {
    const bars = breakoutDay([bar(D1, 590, 105, 106, 104, 105), bar(D1, 595, 105, 106, 104, 105)]);
    const { trades } = runBacktest(bars, { slippageTicks: 1, rules: TRIGGER_ONLY });
    assert.equal(trades[0].entry, 105.25); // MES tick 0.25, long pays up
  });

  it('resolves a bar containing BOTH stop and target as a stop (pessimistic)', () => {
    // Entry 105, stop 97, target 121 — the 09:50 bar spans 95..125.
    const bars = breakoutDay([
      bar(D1, 590, 105, 125, 95, 110),
      bar(D1, 595, 110, 111, 109, 110),
    ]);
    const { trades } = runBacktest(bars, { slippageTicks: 0, rules: TRIGGER_ONLY });
    assert.equal(trades[0].exitReason, 'stop');
    assert.ok(trades[0].r < 0);
  });

  it('takes the target when only the target is touched', () => {
    const bars = breakoutDay([
      bar(D1, 590, 105, 122, 104, 121),
      bar(D1, 595, 121, 122, 120, 121),
    ]);
    const { trades } = runBacktest(bars, { slippageTicks: 0, rules: TRIGGER_ONLY });
    assert.equal(trades[0].exitReason, 'target');
    assert.ok(Math.abs(trades[0].r - 2) < 1e-9);
  });

  it('force-exits at flatByMin when neither level is reached', () => {
    const filler = [];
    for (let m = 590; m <= 690; m += 5) filler.push(bar(D1, m, 105, 106, 104, 105.5));
    const { trades } = runBacktest(breakoutDay(filler), {
      slippageTicks: 0, flatByMin: 690, rules: TRIGGER_ONLY,
    });
    assert.equal(trades[0].exitReason, 'time');
    assert.equal(trades[0].exitMinutes, 690);
  });

  it('exits at the last bar when the session ends mid-trade', () => {
    const bars = breakoutDay([
      bar(D1, 590, 105, 106, 104, 105),
      bar(D1, 595, 105, 106, 104, 105.5),
    ]);
    const { trades } = runBacktest(bars, { slippageTicks: 0, flatByMin: 900, rules: TRIGGER_ONLY });
    assert.equal(trades[0].exitReason, 'session_end');
  });

  it('never trades the first session, which has no prior day for pivots', () => {
    const { trades, sessions } = runBacktest(
      [...priorSession(D0, 92, 88, 90)], { rules: TRIGGER_ONLY },
    );
    assert.equal(sessions, 1);
    assert.equal(trades.length, 0);
  });
});

describe('runBacktest() — first-break-only rule', () => {
  const D0 = '2025-03-03';
  const D1 = '2025-03-04';

  /**
   * Prior day gives P = 90. The first break is a SHORT at 09:45 closing at 97,
   * which fails "price below P". Price then reverses and breaks the OR high at
   * 10:00, which passes. Only the re-arming mode should produce a trade.
   */
  const bars = [
    ...priorSession(D0, 92, 88, 90),
    bar(D1, 570, 100, 102, 98, 100),
    bar(D1, 575, 100, 101, 99, 100),
    bar(D1, 580, 100, 102, 98, 100),
    bar(D1, 585, 99, 99, 96, 97),      // short break of OR low (98) — fails pivot side
    bar(D1, 590, 97, 101, 97, 100),
    bar(D1, 595, 100, 103, 100, 103),  // long break of OR high (102) — passes
    bar(D1, 600, 104, 105, 103, 104),
    bar(D1, 605, 104, 105, 103, 104),
  ];

  const rules = {
    useVwapSide: false, useVwapSlope: false, usePivotRoom: false, useAbovePivot: true,
    minScore: 2, stopMode: 'fixed', fixedStopPoints: 8, targetMode: 'rr', targetRR: 2.0,
  };

  it('skips the day when the first break fails a filter', () => {
    const { trades, skipped } = runBacktest(bars, {
      slippageTicks: 0, firstBreakOnly: true, flatByMin: 900, rules,
    });
    assert.equal(trades.length, 0);
    assert.equal(skipped.length, 1);
    assert.equal(skipped[0].direction, -1);
  });

  it('takes the later qualifying break when re-arming is allowed', () => {
    const { trades } = runBacktest(bars, {
      slippageTicks: 0, firstBreakOnly: false, flatByMin: 900, rules,
    });
    assert.equal(trades.length, 1);
    assert.equal(trades[0].direction, 'long');
    assert.equal(trades[0].signalMinutes, 595);
  });

  it('never exceeds maxTradesPerDay', () => {
    const { bars: synth } = generateBars({ days: 40, seed: 3 });
    const { trades } = runBacktest(synth, {
      firstBreakOnly: false, maxTradesPerDay: 2, rules: TRIGGER_ONLY,
    });
    const perDay = {};
    for (const t of trades) perDay[t.date] = (perDay[t.date] || 0) + 1;
    assert.ok(Object.values(perDay).every((n) => n <= 2));
  });

  it('takes no more trades with firstBreakOnly than without', () => {
    const { bars: synth } = generateBars({ days: 80, seed: 5 });
    const strict = runBacktest(synth, { firstBreakOnly: true, rules: DEFAULT_RULES }).trades.length;
    const loose = runBacktest(synth, { firstBreakOnly: false, rules: DEFAULT_RULES }).trades.length;
    assert.ok(strict <= loose, `expected ${strict} <= ${loose}`);
  });
});

describe('runBacktest() — no manufactured edge', () => {
  it('reports ~zero expectancy on a driftless random walk with no costs', () => {
    const { bars } = generateBars({ days: 600, seed: 11, ...RANDOM_WALK });
    const result = runBacktest(bars, {
      slippageTicks: 0,
      commissionRT: 0,
      entryEndMin: 15 * 60,
      flatByMin: ET.RTH_CLOSE,
      rules: TRIGGER_ONLY,
    });
    const stats = summarize(result.trades, { sessions: result.sessions });

    assert.ok(stats.trades > 400, `expected a meaningful sample, got ${stats.trades}`);

    // A 2R-target / 1R-stop system on a driftless walk wins about a third of
    // the time and nets about nothing. Tolerances are wide enough for sampling
    // noise (SE of expectancy here is roughly 0.06R) but tight enough that a
    // lookahead bug — which inflates expectancy by whole R — would fail.
    assert.ok(
      Math.abs(stats.expectancyR) < 0.2,
      `expectancy ${stats.expectancyR.toFixed(3)}R should be near zero on noise`,
    );
    assert.ok(
      stats.winRate > 0.25 && stats.winRate < 0.45,
      `win rate ${(stats.winRate * 100).toFixed(1)}% should sit near the 33% theoretical value`,
    );
  });

  it('loses money once costs are applied to that same no-edge series', () => {
    const { bars } = generateBars({ days: 600, seed: 11, ...RANDOM_WALK });
    const result = runBacktest(bars, {
      entryEndMin: 15 * 60, flatByMin: ET.RTH_CLOSE, rules: TRIGGER_ONLY,
    });
    const stats = summarize(result.trades, { sessions: result.sessions });
    assert.ok(stats.netUsd < 0, 'commissions and slippage must turn a zero-edge system negative');
  });
});

// ── metrics.js ───────────────────────────────────────────────────────────

describe('summarize()', () => {
  const trades = [
    { r: 2, grossUsd: 80, commissionUsd: 1.24, netUsd: 78.76, exitReason: 'target', direction: 'long' },
    { r: -1, grossUsd: -40, commissionUsd: 1.24, netUsd: -41.24, exitReason: 'stop', direction: 'long' },
    { r: -1, grossUsd: -40, commissionUsd: 1.24, netUsd: -41.24, exitReason: 'stop', direction: 'short' },
  ];

  it('computes win rate, expectancy and profit factor', () => {
    const s = summarize(trades, { sessions: 3 });
    assert.equal(s.trades, 3);
    assert.ok(Math.abs(s.winRate - 1 / 3) < 1e-9);
    assert.equal(s.expectancyR, 0);
    assert.equal(s.profitFactor, 1);
    assert.equal(s.tradesPerSession, 1);
  });

  it('breaks results down by exit reason and direction', () => {
    const s = summarize(trades, { sessions: 3 });
    assert.deepEqual(s.byExitReason, { target: 1, stop: 2 });
    assert.equal(s.byDirection.long.trades, 2);
    assert.equal(s.byDirection.short.winRate, 0);
  });

  it('returns a zeroed summary for an empty trade list', () => {
    const s = summarize([], { sessions: 10 });
    assert.equal(s.trades, 0);
    assert.equal(s.expectancyR, 0);
    assert.equal(s.tradesPerSession, 0);
  });

  it('reports infinite profit factor when there are no losses', () => {
    const s = summarize([{ r: 1, grossUsd: 1, commissionUsd: 0, netUsd: 1, exitReason: 'target', direction: 'long' }]);
    assert.equal(s.profitFactor, Infinity);
  });
});

describe('maxDrawdown() and maxConsecutiveLosses()', () => {
  it('measures the worst peak-to-trough decline', () => {
    assert.equal(maxDrawdown([1, 1, -3, 1]), 3); // peak 2 → trough -1
  });

  it('is zero for a monotonically rising curve', () => {
    assert.equal(maxDrawdown([1, 2, 3]), 0);
  });

  it('counts the longest losing streak', () => {
    const trades = [{ r: 1 }, { r: -1 }, { r: -1 }, { r: -1 }, { r: 2 }, { r: -1 }];
    assert.equal(maxConsecutiveLosses(trades), 3);
  });
});

// ── synthetic.js ─────────────────────────────────────────────────────────

describe('generateBars()', () => {
  it('is deterministic for a given seed', () => {
    const a = generateBars({ days: 3, seed: 99 }).bars;
    const b = generateBars({ days: 3, seed: 99 }).bars;
    assert.deepEqual(a, b);
  });

  it('differs across seeds', () => {
    const a = generateBars({ days: 3, seed: 1 }).bars;
    const b = generateBars({ days: 3, seed: 2 }).bars;
    assert.notDeepEqual(a, b);
  });

  it('emits 78 RTH bars per session, all internally consistent', () => {
    const { bars } = generateBars({ days: 2, seed: 7 });
    assert.equal(bars.length, 156);
    for (const b of bars) {
      assert.ok(b.high >= Math.max(b.open, b.close), 'high must cap open and close');
      assert.ok(b.low <= Math.min(b.open, b.close), 'low must floor open and close');
      assert.ok(b.volume > 0);
    }
  });

  it('starts each session at 09:30 ET and steps in 5-minute bars', () => {
    const { bars } = generateBars({ days: 1, seed: 7 });
    assert.equal(toEt(bars[0].time).minutes, ET.RTH_OPEN);
    assert.equal(bars[1].time - bars[0].time, 5 * 60000);
    assert.equal(toEt(bars.at(-1).time).minutes, 955);
  });

  it('produces only chop days under RANDOM_WALK', () => {
    const { regimes } = generateBars({ days: 20, seed: 4, ...RANDOM_WALK });
    assert.ok(regimes.every((r) => r.regime === 'chop'));
  });

  it('mulberry32 stays inside [0, 1)', () => {
    const rnd = mulberry32(123);
    for (let i = 0; i < 500; i++) {
      const v = rnd();
      assert.ok(v >= 0 && v < 1);
    }
  });
});

// ── csv.js ───────────────────────────────────────────────────────────────

describe('parseCsv()', () => {
  it('reads the semicolon NinjaTrader export format', () => {
    const bars = parseCsv('20250303 093000;6800.25;6805;6798.5;6803.75;12045');
    assert.equal(bars.length, 1);
    assert.deepEqual(toEt(bars[0].time), { date: '2025-03-03', minutes: 570 });
    assert.equal(bars[0].close, 6803.75);
    assert.equal(bars[0].volume, 12045);
  });

  it('reads a headered comma CSV in any column order', () => {
    const text = [
      'volume,close,low,high,open,time',
      '900,105,99,106,100,2025-03-03 09:30',
    ].join('\n');
    const [b] = parseCsv(text);
    assert.equal(b.open, 100);
    assert.equal(b.close, 105);
    assert.equal(b.volume, 900);
  });

  it('honours the UTC timezone option', () => {
    const et = parseCsv('time,open,high,low,close\n2025-03-03 09:30,1,1,1,1')[0];
    const utc = parseCsv('time,open,high,low,close\n2025-03-03 09:30,1,1,1,1', { tz: 'UTC' })[0];
    assert.notEqual(et.time, utc.time);
    assert.equal(toEt(utc.time).minutes, 270); // 09:30 UTC is 04:30 ET
  });

  it('sorts out-of-order rows and drops unparseable ones', () => {
    const text = [
      '20250303 094000;3;3;3;3;1',
      'garbage row without numbers',
      '20250303 093000;1;1;1;1;1',
    ].join('\n');
    const bars = parseCsv(text);
    assert.equal(bars.length, 2);
    assert.ok(bars[0].time < bars[1].time);
  });

  it('throws a useful error when a required column is missing', () => {
    assert.throws(() => parseCsv('time,open,high\n2025-03-03 09:30,1,2'), /is missing a '(low|close|volume)' column/);
  });

  it('round-trips epoch milliseconds', () => {
    const ms = Date.UTC(2025, 2, 3, 14, 30);
    assert.equal(parseTimestamp(String(ms)), ms);
  });
});

// ── variants.js ──────────────────────────────────────────────────────────

describe('variants', () => {
  it('resolves every named variant against the default rules', () => {
    for (const name of Object.keys(VARIANTS)) {
      const { rules, label } = getVariant(name);
      assert.ok(label.length > 0);
      assert.ok(typeof rules.minScore === 'number');
      assert.ok(['fixed', 'atr', 'vwap'].includes(rules.stopMode));
    }
  });

  it('throws on an unknown variant', () => {
    assert.throws(() => getVariant('nope'), /Unknown variant/);
  });
});
