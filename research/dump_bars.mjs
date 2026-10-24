// Dump every bar currently loaded on the active chart to a CSV (time,open,high,low,close,volume).
// usage: node research/dump_bars.mjs OUT.csv
import { evaluate, KNOWN_PATHS } from '../src/connection.js';
import fs from 'fs';
const out = process.argv[2];
const r = await evaluate(`(function(){
  var bars = ${KNOWN_PATHS.mainSeriesBars};
  var a = [], s = bars.firstIndex(), e = bars.lastIndex();
  for (var i = s; i <= e; i++) { var v = bars.valueAt(i); if (v) a.push(v[0]+','+v[1]+','+v[2]+','+v[3]+','+v[4]+','+(v[5]||0)); }
  return {n: a.length, sym: window.TradingViewApi._activeChartWidgetWV.value().symbol(), csv: a.join('\\n')};
})()`);
fs.writeFileSync(out, 'time,open,high,low,close,volume\n' + r.csv + '\n');
console.log(JSON.stringify({symbol: r.sym, bars: r.n, file: out}));
process.exit(0);
