/**
 * Plain-text report formatting for backtest results.
 */

const pct = (x) => `${(x * 100).toFixed(1)}%`;
const r2 = (x) => (Number.isFinite(x) ? x.toFixed(2) : '∞');
const usd = (x) => `${x < 0 ? '-' : ''}$${Math.abs(x).toFixed(0)}`;

function pad(s, width, right = false) {
  const str = String(s);
  if (str.length >= width) return str.slice(0, width);
  return right ? str.padStart(width) : str.padEnd(width);
}

/**
 * Render the variant comparison table.
 *
 * @param {Array<{name:string, label:string, stats:object}>} rows
 * @returns {string}
 */
export function formatComparison(rows) {
  const cols = [
    ['Variant', 24, (r) => r.name],
    ['Trades', 7, (r) => r.stats.trades],
    ['Win%', 7, (r) => pct(r.stats.winRate)],
    ['Exp(R)', 8, (r) => r2(r.stats.expectancyR)],
    ['TotalR', 8, (r) => r2(r.stats.totalR)],
    ['PF', 6, (r) => r2(r.stats.profitFactor)],
    ['MaxDD(R)', 9, (r) => r2(r.stats.maxDrawdownR)],
    ['Net$', 9, (r) => usd(r.stats.netUsd)],
    ['Trd/Day', 8, (r) => r.stats.tradesPerSession.toFixed(2)],
  ];

  const lines = [];
  lines.push(cols.map(([h, w], i) => pad(h, w, i > 0)).join(' '));
  lines.push(cols.map(([, w]) => '─'.repeat(w)).join(' '));
  for (const row of rows) {
    lines.push(cols.map(([, w, fn], i) => pad(fn(row), w, i > 0)).join(' '));
  }
  return lines.join('\n');
}

/**
 * Render a detail block for a single variant.
 *
 * @param {string} name
 * @param {object} stats
 * @param {{maxConsecLosses?: number}} [extra]
 * @returns {string}
 */
export function formatDetail(name, stats, extra = {}) {
  const lines = [];
  lines.push(`── ${name} ${'─'.repeat(Math.max(0, 60 - name.length))}`);
  lines.push(`  Sessions            ${stats.sessions}`);
  lines.push(`  Trades              ${stats.trades}  (${stats.tradesPerSession.toFixed(2)}/session)`);
  lines.push(`  Win rate            ${pct(stats.winRate)}  (${stats.wins}W / ${stats.losses}L)`);
  lines.push(`  Expectancy          ${r2(stats.expectancyR)} R per trade`);
  lines.push(`  Avg win / avg loss  ${r2(stats.avgWinR)}R / ${r2(stats.avgLossR)}R`);
  lines.push(`  Profit factor       ${r2(stats.profitFactor)}`);
  lines.push(`  Total               ${r2(stats.totalR)} R`);
  lines.push(`  Max drawdown        ${r2(stats.maxDrawdownR)} R  (${usd(stats.maxDrawdownUsd)})`);
  if (extra.maxConsecLosses != null) {
    lines.push(`  Max consec losses   ${extra.maxConsecLosses}`);
  }
  lines.push(`  Gross / comm / net  ${usd(stats.grossUsd)} / ${usd(stats.commissionUsd)} / ${usd(stats.netUsd)}`);
  const exits = Object.entries(stats.byExitReason)
    .map(([k, v]) => `${k}=${v}`).join('  ');
  if (exits) lines.push(`  Exits               ${exits}`);
  for (const [dir, d] of Object.entries(stats.byDirection)) {
    lines.push(`  ${pad(dir, 18)}  ${d.trades} trades, ${pct(d.winRate)} win, ${r2(d.expectancyR)}R exp`);
  }
  return lines.join('\n');
}

/**
 * The warning that must accompany every synthetic-data result.
 * @returns {string}
 */
export function syntheticWarning() {
  return [
    '',
    '⚠  SYNTHETIC DATA — these numbers measure the ENGINE, not the market.',
    '   The bars are a seeded random walk with a hand-picked mix of trend and',
    '   chop days. Any edge shown here is an artifact of that generator.',
    '   Load real 5-minute data (NinjaTrader export → loadCsv) before drawing',
    '   a single conclusion about whether this strategy makes money.',
    '',
  ].join('\n');
}
