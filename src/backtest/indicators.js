/**
 * Indicators used by the ORB + VWAP + Fib-pivot strategy.
 *
 * Every function is pure and causal: the value at index i is computed only
 * from bars 0..i. That property is what keeps the backtest free of lookahead.
 */

/**
 * Session-anchored VWAP.
 *
 * Resets at the start of each session. Uses the typical price (H+L+C)/3
 * weighted by volume, which is the convention TradingView and NinjaTrader
 * both default to.
 *
 * @param {Array<{high:number,low:number,close:number,volume:number}>} bars one session
 * @returns {number[]} VWAP value per bar
 */
export function sessionVwap(bars) {
  const out = new Array(bars.length);
  let pv = 0;
  let vol = 0;
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i];
    const typical = (b.high + b.low + b.close) / 3;
    const v = b.volume > 0 ? b.volume : 1; // guard zero-volume bars
    pv += typical * v;
    vol += v;
    out[i] = pv / vol;
  }
  return out;
}

/**
 * VWAP slope over a lookback, expressed in price units per bar.
 *
 * @param {number[]} vwap
 * @param {number} i index
 * @param {number} [lookback=3]
 * @returns {number} 0 when there is not enough history
 */
export function vwapSlope(vwap, i, lookback = 3) {
  if (i < lookback) return 0;
  return (vwap[i] - vwap[i - lookback]) / lookback;
}

/**
 * Wilder's ATR over a continuous bar series.
 *
 * Computed across the whole series (not per session) so the first bars of a
 * session inherit volatility context from the prior one. Values before the
 * period is filled are null.
 *
 * @param {Array<{high:number,low:number,close:number}>} bars
 * @param {number} [period=14]
 * @returns {Array<number|null>}
 */
export function atr(bars, period = 14) {
  const out = new Array(bars.length).fill(null);
  if (bars.length === 0) return out;

  const tr = new Array(bars.length);
  tr[0] = bars[0].high - bars[0].low;
  for (let i = 1; i < bars.length; i++) {
    const prevClose = bars[i - 1].close;
    tr[i] = Math.max(
      bars[i].high - bars[i].low,
      Math.abs(bars[i].high - prevClose),
      Math.abs(bars[i].low - prevClose),
    );
  }

  if (bars.length < period) return out;

  let sum = 0;
  for (let i = 0; i < period; i++) sum += tr[i];
  let prev = sum / period;
  out[period - 1] = prev;
  for (let i = period; i < bars.length; i++) {
    prev = (prev * (period - 1) + tr[i]) / period;
    out[i] = prev;
  }
  return out;
}

/**
 * Fibonacci pivot levels from a prior session's high/low/close.
 *
 *   P  = (H + L + C) / 3
 *   Rn = P + fib(n) * (H - L)
 *   Sn = P - fib(n) * (H - L)   with fib = [0.382, 0.618, 1.000]
 *
 * @param {{high:number, low:number, close:number}} prior
 * @returns {{p:number,r1:number,r2:number,r3:number,s1:number,s2:number,s3:number,range:number}}
 */
export function fibPivots(prior) {
  const { high, low, close } = prior;
  const range = high - low;
  const p = (high + low + close) / 3;
  return {
    p,
    range,
    r1: p + 0.382 * range,
    r2: p + 0.618 * range,
    r3: p + 1.0 * range,
    s1: p - 0.382 * range,
    s2: p - 0.618 * range,
    s3: p - 1.0 * range,
  };
}

/**
 * Nearest pivot level strictly beyond a price in the trade's direction.
 *
 * This is the "is there room?" input — the level the trade would be aiming at.
 *
 * @param {object} pivots from fibPivots()
 * @param {number} price
 * @param {1|-1} direction 1 = long (look up), -1 = short (look down)
 * @returns {{name: string, price: number}|null} null when price is beyond R3/S3
 */
export function nextPivot(pivots, price, direction) {
  const names = direction === 1
    ? ['p', 'r1', 'r2', 'r3']
    : ['p', 's1', 's2', 's3'];
  let best = null;
  for (const name of names) {
    const level = pivots[name];
    const beyond = direction === 1 ? level > price : level < price;
    if (!beyond) continue;
    if (!best || Math.abs(level - price) < Math.abs(best.price - price)) {
      best = { name, price: level };
    }
  }
  return best;
}

/**
 * High/low of the opening range.
 *
 * @param {Array<{etMinutes:number,high:number,low:number}>} sessionBars
 * @param {number} startMin inclusive
 * @param {number} endMin exclusive
 * @returns {{high:number, low:number, bars:number}|null} null when no bars fall in the window
 */
export function openingRange(sessionBars, startMin, endMin) {
  let high = -Infinity;
  let low = Infinity;
  let count = 0;
  for (const b of sessionBars) {
    if (b.etMinutes < startMin || b.etMinutes >= endMin) continue;
    if (b.high > high) high = b.high;
    if (b.low < low) low = b.low;
    count++;
  }
  if (count === 0) return null;
  return { high, low, bars: count };
}
