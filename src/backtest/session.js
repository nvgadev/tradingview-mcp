/**
 * Session helpers — Eastern Time conversion and RTH session grouping.
 *
 * Bars carry `time` as epoch milliseconds (UTC). All strategy rules are
 * expressed in ET minutes-from-midnight (09:30 = 570) so DST is handled by
 * Intl rather than by hand-rolled offsets.
 */

const ET_FMT = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hour12: false,
});

/** Minutes-from-midnight ET for common session boundaries. */
export const ET = {
  RTH_OPEN: 9 * 60 + 30,   // 570
  OR_END: 9 * 60 + 45,     // 585
  VWAP_WARM: 10 * 60,      // 600
  LAST_ENTRY: 11 * 60,     // 660
  MIDDAY: 11 * 60 + 30,    // 690
  RTH_CLOSE: 16 * 60,      // 960
};

/**
 * Convert epoch ms to ET calendar date + minutes-from-midnight.
 * @param {number} ms
 * @returns {{date: string, minutes: number}} date as 'YYYY-MM-DD'
 */
export function toEt(ms) {
  const g = {};
  for (const p of ET_FMT.formatToParts(new Date(ms))) {
    if (p.type !== 'literal') g[p.type] = p.value;
  }
  // Some ICU builds emit hour '24' for midnight under hour12:false.
  const hour = Number(g.hour) % 24;
  return {
    date: `${g.year}-${g.month}-${g.day}`,
    minutes: hour * 60 + Number(g.minute),
  };
}

/**
 * Annotate bars with ET date/minutes in place-free fashion and group by session.
 *
 * Bars are assumed sorted ascending by time. Sessions are keyed by ET calendar
 * date and contain only bars inside [rthOpen, rthClose).
 *
 * @param {Array<{time:number,open:number,high:number,low:number,close:number,volume:number}>} bars
 * @param {{rthOpen?: number, rthClose?: number}} [opts]
 * @returns {Array<{date: string, bars: Array<object>}>} sessions in chronological order
 */
export function groupSessions(bars, opts = {}) {
  const rthOpen = opts.rthOpen ?? ET.RTH_OPEN;
  const rthClose = opts.rthClose ?? ET.RTH_CLOSE;
  const byDate = new Map();

  for (let i = 0; i < bars.length; i++) {
    const b = bars[i];
    const { date, minutes } = toEt(b.time);
    if (minutes < rthOpen || minutes >= rthClose) continue;
    if (!byDate.has(date)) byDate.set(date, []);
    byDate.get(date).push({ ...b, etMinutes: minutes, etDate: date, index: i });
  }

  return [...byDate.entries()]
    .sort((a, b) => (a[0] < b[0] ? -1 : 1))
    .map(([date, sessionBars]) => ({ date, bars: sessionBars }));
}
