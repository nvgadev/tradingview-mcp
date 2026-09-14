/**
 * Deterministic synthetic bar generator.
 *
 * ⚠️  READ THIS BEFORE BELIEVING ANY NUMBER PRODUCED FROM SYNTHETIC DATA.
 *
 * This generator exists to test the ENGINE, not the STRATEGY. It produces
 * random walks with a hand-chosen mix of trending and choppy days. A strategy
 * that looks profitable on this data has only demonstrated that it can exploit
 * the generator's assumptions. Real edge can only be measured on real market
 * data — export it from NinjaTrader and load it with `loadCsv`.
 */

import { toEt } from './session.js';

/**
 * Small, fast, seedable PRNG (mulberry32). Deterministic across runs, so tests
 * and reported numbers are reproducible.
 * @param {number} seed
 * @returns {() => number} uniform in [0, 1)
 */
export function mulberry32(seed) {
  let a = seed >>> 0;
  return function next() {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Box–Muller standard normal from a uniform generator. */
function gauss(rnd) {
  const u = Math.max(rnd(), 1e-12);
  const v = rnd();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

/**
 * Epoch ms of 09:30 ET on a given calendar date, DST-correct.
 * @param {number} y @param {number} m 1-12 @param {number} d
 * @returns {number}
 */
function sessionOpenMs(y, m, d) {
  // 14:30 UTC is 09:30 or 10:30 ET depending on DST — correct by the delta.
  const guess = Date.UTC(y, m - 1, d, 14, 30);
  const { minutes } = toEt(guess);
  return guess + (570 - minutes) * 60000;
}

export const REGIMES = {
  TREND_UP: 'trend_up',
  TREND_DOWN: 'trend_down',
  CHOP: 'chop',
};

/**
 * Options that produce a pure driftless random walk — no trend, no mean
 * reversion. A strategy run over these bars must show expectancy ~0 before
 * costs; that is how the engine proves it is not manufacturing edge.
 */
export const RANDOM_WALK = {
  regimeMix: { trendUp: 0, trendDown: 0 },
  trendDrift: 0,
  pullStrength: 0,
};

/**
 * Generate continuous 5-minute RTH bars across N trading days.
 *
 * @param {object} [opts]
 * @param {number} [opts.days=120] number of sessions
 * @param {number} [opts.seed=42]
 * @param {number} [opts.startPrice=6800]
 * @param {string} [opts.startDate='2025-01-06'] a Monday
 * @param {{trendUp:number, trendDown:number}} [opts.regimeMix] probabilities; remainder is chop
 * @param {number} [opts.trendDrift=0.35] points per bar on a trend day
 * @param {number} [opts.noise=2.2] per-bar standard deviation in points
 * @param {number} [opts.pullStrength=0.02] mean-reversion pull toward the session
 *        open on chop days. Set to 0 together with `regimeMix: {trendUp:0,
 *        trendDown:0}` and `trendDrift: 0` to get a PURE DRIFTLESS RANDOM WALK —
 *        the fixture the engine's no-edge validation test depends on.
 * @returns {{bars: Array<object>, regimes: Array<{date:string, regime:string}>}}
 */
export function generateBars(opts = {}) {
  const {
    days = 120,
    seed = 42,
    startPrice = 6800,
    startDate = '2025-01-06',
    regimeMix = { trendUp: 0.2, trendDown: 0.2 },
    trendDrift = 0.35,
    noise = 2.2,
    pullStrength = 0.02,
  } = opts;

  const rnd = mulberry32(seed);
  const bars = [];
  const regimes = [];
  const BARS_PER_SESSION = 78; // 09:30 → 16:00 on a 5-minute chart
  const SUBSTEPS = 5;          // intrabar path resolution for realistic H/L

  let price = startPrice;
  const [sy, sm, sd] = startDate.split('-').map(Number);
  const cursor = new Date(Date.UTC(sy, sm - 1, sd));

  for (let day = 0; day < days; day++) {
    // Skip weekends. Holidays are not modelled — irrelevant for engine tests.
    while (cursor.getUTCDay() === 0 || cursor.getUTCDay() === 6) {
      cursor.setUTCDate(cursor.getUTCDate() + 1);
    }
    const y = cursor.getUTCFullYear();
    const m = cursor.getUTCMonth() + 1;
    const d = cursor.getUTCDate();
    const openMs = sessionOpenMs(y, m, d);

    const roll = rnd();
    let regime;
    if (roll < regimeMix.trendUp) regime = REGIMES.TREND_UP;
    else if (roll < regimeMix.trendUp + regimeMix.trendDown) regime = REGIMES.TREND_DOWN;
    else regime = REGIMES.CHOP;

    const drift = regime === REGIMES.TREND_UP ? trendDrift
      : regime === REGIMES.TREND_DOWN ? -trendDrift
        : 0;
    const sessionOpen = price;

    regimes.push({ date: `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`, regime });

    for (let b = 0; b < BARS_PER_SESSION; b++) {
      const open = price;
      let high = open;
      let low = open;
      let last = open;

      for (let s = 0; s < SUBSTEPS; s++) {
        // Chop days pull back toward the session open; trend days do not.
        const pull = regime === REGIMES.CHOP ? (sessionOpen - last) * pullStrength : 0;
        last += drift / SUBSTEPS + pull + gauss(rnd) * (noise / Math.sqrt(SUBSTEPS));
        if (last > high) high = last;
        if (last < low) low = last;
      }

      price = last;
      bars.push({
        time: openMs + b * 5 * 60000,
        open: round2(open),
        high: round2(high),
        low: round2(low),
        close: round2(last),
        // Volume smiles: heavy at the open, lighter midday.
        volume: Math.round(1500 + 3500 * Math.exp(-b / 12) + rnd() * 400),
      });
    }

    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }

  return { bars, regimes };
}

function round2(x) {
  return Math.round(x * 100) / 100;
}
