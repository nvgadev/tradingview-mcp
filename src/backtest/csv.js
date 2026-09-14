/**
 * CSV loader for real market data.
 *
 * Handles the two shapes you are most likely to have on disk:
 *
 *   NinjaTrader export   20250106 093000;6800.25;6805.00;6798.50;6803.75;12045
 *   Generic CSV          time,open,high,low,close,volume  (with header row)
 *
 * Timestamps without an explicit zone are interpreted as Eastern Time by
 * default, because that is what exchange-time exports contain. Pass
 * `{ tz: 'UTC' }` if yours are UTC — getting this wrong silently shifts every
 * session window and quietly invalidates the whole backtest.
 */

import { readFileSync } from 'node:fs';
import { toEt } from './session.js';

const NT_TIMESTAMP = /^(\d{4})(\d{2})(\d{2})[ T]?(\d{2})(\d{2})(\d{2})?$/;
const PLAIN_TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/;
const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

function dayNumber(dateStr) {
  const [y, m, d] = dateStr.split('-').map(Number);
  return Date.UTC(y, m - 1, d) / 86400000;
}

/**
 * Convert a wall-clock Eastern Time stamp to epoch ms, DST-correct.
 * @returns {number}
 */
function etWallClockToMs(y, m, d, hh, mm) {
  const targetDate = `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  const targetTotal = dayNumber(targetDate) * 1440 + hh * 60 + mm;
  let ms = Date.UTC(y, m - 1, d, hh, mm);
  // Two passes converge even across a DST boundary.
  for (let pass = 0; pass < 2; pass++) {
    const et = toEt(ms);
    const gotTotal = dayNumber(et.date) * 1440 + et.minutes;
    const deltaMin = targetTotal - gotTotal;
    if (deltaMin === 0) break;
    ms += deltaMin * 60000;
  }
  return ms;
}

/**
 * Parse one timestamp field into epoch ms.
 * @param {string} raw
 * @param {string} tz 'ET' | 'UTC'
 * @returns {number|null}
 */
export function parseTimestamp(raw, tz = 'ET') {
  const s = String(raw).trim().replace(/Z$/, '');
  if (/^\d{12,13}$/.test(s)) return Number(s); // already epoch ms

  let match = NT_TIMESTAMP.exec(s);
  if (!match) match = PLAIN_TIMESTAMP.exec(s);
  if (match) {
    const [, y, m, d, hh, mm] = match.map(Number);
    return tz === 'UTC' ? Date.UTC(y, m - 1, d, hh, mm) : etWallClockToMs(y, m, d, hh, mm);
  }

  match = DATE_ONLY.exec(s);
  if (match) {
    const [, y, m, d] = match.map(Number);
    return tz === 'UTC' ? Date.UTC(y, m - 1, d) : etWallClockToMs(y, m, d, 0, 0);
  }

  const parsed = Date.parse(raw);
  return Number.isNaN(parsed) ? null : parsed;
}

/**
 * Parse CSV text into bars.
 *
 * @param {string} text
 * @param {{tz?: string}} [opts]
 * @returns {Array<{time:number,open:number,high:number,low:number,close:number,volume:number}>}
 *          sorted ascending by time
 */
export function parseCsv(text, opts = {}) {
  const tz = opts.tz ?? 'ET';
  const lines = text.split(/\r?\n/).filter((l) => l.trim().length > 0);
  if (lines.length === 0) return [];

  const delimiter = lines[0].includes(';') ? ';' : ',';
  const firstCells = lines[0].split(delimiter);
  const hasHeader = /[a-zA-Z]{2,}/.test(firstCells[1] ?? '') || /open/i.test(lines[0]);

  let cols = { time: 0, open: 1, high: 2, low: 3, close: 4, volume: 5 };
  let start = 0;

  if (hasHeader) {
    const header = firstCells.map((h) => h.trim().toLowerCase());
    const find = (...names) => {
      for (const n of names) {
        const idx = header.indexOf(n);
        if (idx !== -1) return idx;
      }
      return -1;
    };
    cols = {
      time: find('time', 'date', 'datetime', 'timestamp'),
      open: find('open', 'o'),
      high: find('high', 'h'),
      low: find('low', 'l'),
      close: find('close', 'c', 'last'),
      volume: find('volume', 'vol', 'v'),
    };
    for (const [key, idx] of Object.entries(cols)) {
      if (idx === -1 && key !== 'volume') {
        throw new Error(`CSV header is missing a '${key}' column. Got: ${header.join(', ')}`);
      }
    }
    start = 1;
  }

  const bars = [];
  for (let i = start; i < lines.length; i++) {
    const cells = lines[i].split(delimiter);
    const time = parseTimestamp(cells[cols.time], tz);
    if (time == null) continue;
    const bar = {
      time,
      open: Number(cells[cols.open]),
      high: Number(cells[cols.high]),
      low: Number(cells[cols.low]),
      close: Number(cells[cols.close]),
      volume: cols.volume >= 0 ? Number(cells[cols.volume]) || 0 : 0,
    };
    if ([bar.open, bar.high, bar.low, bar.close].some(Number.isNaN)) continue;
    bars.push(bar);
  }

  bars.sort((a, b) => a.time - b.time);
  return bars;
}

/**
 * Read and parse a CSV file from disk.
 * @param {string} path
 * @param {{tz?: string}} [opts]
 * @returns {Array<object>}
 */
export function loadCsv(path, opts = {}) {
  return parseCsv(readFileSync(path, 'utf8'), opts);
}
