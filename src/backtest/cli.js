#!/usr/bin/env node
/**
 * Backtest runner.
 *
 *   node src/backtest/cli.js                        # synthetic data, all variants
 *   node src/backtest/cli.js --days 250 --seed 7
 *   node src/backtest/cli.js --csv data/MES.csv --symbol MES
 *   node src/backtest/cli.js --variant orb_vwap_pivot --detail
 *   node src/backtest/cli.js --csv data/MES.csv --tz UTC --entry-start 600
 */

import {
  compareVariants, generateBars, loadCsv,
  formatComparison, formatDetail, syntheticWarning, VARIANTS,
} from './index.js';

function parseArgs(argv) {
  const args = { flags: new Set() };
  for (let i = 2; i < argv.length; i++) {
    const token = argv[i];
    if (!token.startsWith('--')) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith('--')) {
      args.flags.add(key);
    } else {
      args[key] = next;
      i++;
    }
  }
  return args;
}

function main() {
  const args = parseArgs(process.argv);

  if (args.flags.has('help')) {
    console.log(`Variants: ${Object.keys(VARIANTS).join(', ')}`);
    console.log('Options: --csv PATH --tz ET|UTC --symbol MES --contracts 1 --days N --seed N');
    console.log('         --variant NAME --detail --entry-start MIN --entry-end MIN --max-trades N');
    return;
  }

  let bars;
  let synthetic = false;
  if (args.csv) {
    bars = loadCsv(args.csv, { tz: args.tz || 'ET' });
    console.log(`Loaded ${bars.length} bars from ${args.csv}`);
  } else {
    synthetic = true;
    const generated = generateBars({
      days: Number(args.days || 120),
      seed: Number(args.seed || 42),
    });
    bars = generated.bars;
    const counts = {};
    for (const { regime } of generated.regimes) counts[regime] = (counts[regime] || 0) + 1;
    console.log(`Generated ${bars.length} synthetic bars over ${generated.regimes.length} sessions`);
    console.log(`Regime mix: ${Object.entries(counts).map(([k, v]) => `${k}=${v}`).join('  ')}`);
  }

  if (bars.length === 0) {
    console.error('No bars to test.');
    process.exitCode = 1;
    return;
  }

  const config = { symbol: args.symbol || 'MES', contracts: Number(args.contracts || 1) };
  if (args['entry-start']) config.entryStartMin = Number(args['entry-start']);
  if (args['entry-end']) config.entryEndMin = Number(args['entry-end']);
  if (args['max-trades']) config.maxTradesPerDay = Number(args['max-trades']);

  const variants = args.variant ? [args.variant] : undefined;
  const results = compareVariants(bars, { variants, config });

  console.log(`\nContract ${config.symbol}  x${config.contracts} contract(s)\n`);
  console.log(formatComparison(results));
  console.log('');

  if (args.flags.has('detail')) {
    for (const row of results) {
      console.log('');
      console.log(formatDetail(`${row.name} — ${row.label}`, row.stats, {
        maxConsecLosses: row.maxConsecLosses,
      }));
    }
    console.log('');
  }

  if (synthetic) console.log(syntheticWarning());
}

main();
