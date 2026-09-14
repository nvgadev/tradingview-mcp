#!/usr/bin/env node
/**
 * One-command TradingView setup for the ORB + VWAP + Fib-pivot strategy.
 *
 *   node scripts/setup_orb_layout.js
 *   node scripts/setup_orb_layout.js --symbol "CME_MINI:MES1!" --tf 5
 *   node scripts/setup_orb_layout.js --layout "ORB Lab" --save
 *
 * Requires TradingView Desktop running with CDP on port 9222 (`tv launch`).
 *
 * What it does, in order:
 *   1. health check
 *   2. switch to the named chart layout, creating it if it does not exist
 *   3. set symbol and timeframe
 *   4. open a new Pine *strategy* and inject pine/orb_vwap_pivot.pine
 *   5. compile and read back any compilation errors
 *   6. open the Strategy Tester and screenshot it
 *
 * Writes to your TradingView account: creating a layout, and `--save` (which
 * saves the Pine script to your TradingView cloud). Both are off the happy path
 * of read-only inspection, so the script says what it is doing at each step.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import * as health from '../src/core/health.js';
import * as ui from '../src/core/ui.js';
import * as chart from '../src/core/chart.js';
import * as pine from '../src/core/pine.js';
import * as capture from '../src/core/capture.js';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));

function parseArgs(argv) {
  const args = { flags: new Set() };
  for (let i = 2; i < argv.length; i++) {
    const token = argv[i];
    if (!token.startsWith('--')) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith('--')) args.flags.add(key);
    else { args[key] = next; i++; }
  }
  return args;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let step = 0;
function log(message) {
  console.log(`\n[${++step}] ${message}`);
}
function detail(message) {
  console.log(`    ${message}`);
}

/**
 * Switch to a layout, creating it through TradingView's own save-as API when it
 * does not exist yet. Returns how the layout was obtained.
 * @returns {Promise<'switched'|'created'|'manual'>}
 */
async function ensureLayout(name) {
  try {
    const result = await ui.layoutSwitch({ name });
    detail(`switched to existing layout "${result.layout}"`);
    return 'switched';
  } catch {
    detail(`no layout named "${name}" — creating it`);
  }

  const created = await ui.uiEvaluate({
    expression: `
      (function () {
        var api = window.TradingViewApi;
        if (!api) return { ok: false, reason: 'TradingViewApi not available' };
        var target = ${JSON.stringify(name)};
        if (typeof api.saveChartAs === 'function') {
          api.saveChartAs(target);
          return { ok: true, method: 'saveChartAs' };
        }
        if (typeof api.saveChart === 'function') {
          api.saveChart({ chartName: target });
          return { ok: true, method: 'saveChart' };
        }
        return { ok: false, reason: 'no save-as API on this TradingView build' };
      })()
    `,
  });

  const outcome = created?.result ?? created;
  if (outcome?.ok) {
    await sleep(2500);
    detail(`created layout "${name}" via ${outcome.method}`);
    return 'created';
  }

  detail(`could not create automatically: ${outcome?.reason || 'unknown'}`);
  detail('Create it by hand once — chart toolbar → layout name → "Save as…" →');
  detail(`name it "${name}" — then re-run this script.`);
  return 'manual';
}

async function main() {
  const args = parseArgs(process.argv);
  const layoutName = args.layout || 'ORB VWAP FIB';
  const symbol = args.symbol || 'CME_MINI:MES1!';
  const timeframe = args.tf || '5';
  const pinePath = args.pine || join(ROOT, 'pine', 'orb_vwap_pivot.pine');

  console.log('ORB + VWAP + Fib pivot — TradingView setup');
  console.log(`  layout    ${layoutName}`);
  console.log(`  symbol    ${symbol}`);
  console.log(`  timeframe ${timeframe}m`);
  console.log(`  script    ${pinePath}`);

  log('Checking the TradingView connection');
  let hc;
  try {
    hc = await health.healthCheck();
  } catch (err) {
    hc = { success: false, error: err.message };
  }
  if (!hc?.success) {
    console.error(`    TradingView is not reachable on CDP port 9222 (${hc.error}).`);
    console.error('    Start it with:  npm run tv -- launch');
    console.error('    Already running? Relaunch it with remote debugging enabled —');
    console.error('    the plain desktop shortcut does not open the CDP port.');
    process.exitCode = 1;
    return;
  }
  detail(`connected — ${hc.symbol ?? 'chart ready'}`);

  log(`Preparing layout "${layoutName}"`);
  const layoutState = await ensureLayout(layoutName);
  if (layoutState === 'manual') {
    process.exitCode = 1;
    return;
  }

  log(`Setting ${symbol} @ ${timeframe}m`);
  await chart.setSymbol({ symbol });
  await sleep(1500);
  await chart.setTimeframe({ timeframe });
  await sleep(1500);
  const state = await chart.getState();
  detail(`chart now on ${state?.symbol} ${state?.timeframe}`);

  log('Opening a new Pine strategy and injecting the source');
  await pine.newScript({ type: 'strategy' });
  await sleep(2000);
  const source = readFileSync(pinePath, 'utf-8');
  await pine.setSource({ source });
  detail(`injected ${source.split('\n').length} lines`);

  log('Compiling');
  const compiled = await pine.smartCompile();
  detail(`compile action: ${compiled?.action ?? compiled?.button ?? 'clicked'}`);
  await sleep(3000);

  const errors = await pine.getErrors();
  const list = errors?.errors ?? [];
  if (list.length > 0) {
    console.error(`\n    ${list.length} compilation error(s):`);
    for (const e of list) {
      console.error(`      line ${e.line ?? '?'}: ${e.message ?? e}`);
    }
    console.error('\n    Fix these in pine/orb_vwap_pivot.pine and re-run.');
    process.exitCode = 1;
    return;
  }
  detail('compiled with no errors');

  if (args.flags.has('save')) {
    log('Saving the script to your TradingView cloud');
    await pine.save();
    await sleep(1500);
    detail('saved');
  }

  log('Opening the Strategy Tester');
  await ui.openPanel({ panel: 'strategy-tester', action: 'open' });
  await sleep(4000);

  log('Capturing the Strategy Tester');
  const shot = await capture.captureScreenshot({
    region: 'strategy_tester',
    filename: 'orb_vwap_fib_results',
    waitForRender: true,
  });
  detail(`saved ${shot?.path ?? shot?.filename ?? 'screenshot'}`);

  console.log('\nDone. Read the Strategy Tester "Performance Summary" tab.');
  console.log('Net Profit alone means little — go to the Overview tab and read');
  console.log('Profit Factor and Max Drawdown, and check Total Closed Trades is');
  console.log('large enough that the result is not noise.');
  console.log('\nTo isolate each filter, open the strategy Settings → Inputs and');
  console.log('switch the four filter checkboxes off one at a time, re-reading');
  console.log('the Performance Summary each time.');
}

main().then(
  () => process.exit(process.exitCode ?? 0),
  (err) => {
    console.error(`\nFailed: ${err.message}`);
    process.exit(1);
  },
);
