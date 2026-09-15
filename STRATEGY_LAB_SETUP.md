# Strategy Lab — Full Setup

How to set this up on a machine that has TradingView Desktop, so you can type
plain-language instructions ("build me an ORB strategy with a VWAP filter,
compile it, show me the results") and the agent writes the Pine, compiles it,
reads the errors, fixes them, and runs the Strategy Tester for you.

That loop needs three things connected:

```
Claude Code  ──MCP(stdio)──>  this repo's server  ──CDP:9222──>  TradingView Desktop
```

If any link is missing the agent can write Pine but cannot compile or test it.

---

## 0. What you need

| | |
|---|---|
| **TradingView Desktop** | The *desktop app*, not the website. The browser version has no CDP port. |
| **Node.js 18+** | `node --version` |
| **Claude Code** | The CLI, the desktop app, or an IDE extension |
| **A TradingView account** | Free works; Pine and the Strategy Tester are on all tiers. Deep Backtesting is paid. |

---

## 1. Fastest path — hand the whole job to the agent

On the machine with TradingView, open Claude Code in an empty folder and paste
this. It does steps 2–5 for you.

> Set up the TradingView MCP server so you can drive my TradingView Desktop.
>
> 1. Clone `https://github.com/nvgadev/tradingview-mcp.git` into `~/tradingview-mcp`
>    and check out the branch `claude/swing-trading-strategy-vns2ph`. Run `npm install`.
> 2. Read `SETUP_GUIDE.md` in that repo and follow it to register the MCP server
>    in my Claude Code config. Merge into the existing `mcpServers` object — do
>    not overwrite other servers.
> 3. Tell me to restart Claude Code, and what to run to verify the connection.
>
> Do not launch TradingView yet — tell me when to do it and with which command
> for my OS.

Then restart Claude Code and go to **step 5** to verify.

If you would rather do it by hand, steps 2–5 below are the same thing.

---

## 2. Install

```bash
git clone https://github.com/nvgadev/tradingview-mcp.git ~/tradingview-mcp
cd ~/tradingview-mcp
git checkout claude/swing-trading-strategy-vns2ph
npm install
```

The branch matters — it carries `pine/orb_vwap_pivot.pine`, the setup script,
and the offline backtest module. `main` does not.

---

## 3. Register the MCP server

Add to `~/.claude/.mcp.json` (global) or `.mcp.json` in your project folder.
If the file already exists, merge this entry in; do not replace the file.

```json
{
  "mcpServers": {
    "tradingview": {
      "command": "node",
      "args": ["/absolute/path/to/tradingview-mcp/src/server.js"]
    }
  }
}
```

Use the real absolute path — `~` is not expanded here. On Windows use forward
slashes or escaped backslashes: `"C:/Users/you/tradingview-mcp/src/server.js"`.

**Restart Claude Code.** MCP servers are only loaded at startup.

---

## 4. Launch TradingView with the debug port

The normal desktop shortcut does **not** open the CDP port. It has to be
launched with the flag, every time.

**macOS**
```bash
/Applications/TradingView.app/Contents/MacOS/TradingView --remote-debugging-port=9222
# or: ~/tradingview-mcp/scripts/launch_tv_debug_mac.sh
```

**Windows** — ships as an MSIX package now, so use the script rather than a path:
```bat
cd %USERPROFILE%\tradingview-mcp
scripts\launch_tv_debug.bat
```
If Windows says **"Access is denied"** launching from `WindowsApps`, ask the
agent to run the `tv_launch` tool instead — it copies the package to
`%LOCALAPPDATA%` once (~330 MB, keeps your login and layouts) and launches from
the copy. Never run `icacls` on `WindowsApps`; it fails and can break app servicing.

**Linux**
```bash
/opt/TradingView/tradingview --remote-debugging-port=9222
# or: ~/tradingview-mcp/scripts/launch_tv_debug_linux.sh
```

Easiest of all: once the MCP server is connected, just say **"launch TradingView"**
and the agent runs `tv_launch`, which auto-detects the install on all three platforms.

---

## 5. Verify — do not skip this

```bash
cd ~/tradingview-mcp
node src/cli/index.js status
```

Want: `"success": true`. If you get `CDP connection failed`, TradingView is not
running with the flag — redo step 4.

Then in Claude Code, say: **"run a TradingView health check and tell me the symbol
and timeframe on my chart."** If it reports your actual chart, all three links
are connected and you are ready.

---

## 6. The build → compile → test loop

This is the part you wanted. Once connected, you describe what you want in
plain language and the agent does the round trip. Things that work:

**Load the strategy that already exists in this repo**
> Create a new chart layout called "ORB Lab", set it to CME_MINI:MES1! on the
> 5-minute chart, load `pine/orb_vwap_pivot.pine` into a new Pine strategy,
> compile it, and open the Strategy Tester. If it fails to compile, show me the
> errors and fix them.

Or just run the script that does exactly that:
```bash
node scripts/setup_orb_layout.js
node scripts/setup_orb_layout.js --symbol "CME_MINI:MNQ1!" --layout "MNQ Lab"
```

**Change the strategy**
> Add a filter that skips the trade when the opening range is wider than 1.5x
> ATR. Recompile and tell me what it did to Profit Factor and trade count.

**Run the A/B that actually matters**
> Run the strategy four times, each time changing only the filter inputs:
> (1) all four filters off with min score 1, (2) VWAP side and slope on with
> score 3, (3) all on with score 4, (4) all on with score 5. After each run read
> the Strategy Tester and give me Total Closed Trades, Profit Factor,
> Net Profit and Max Drawdown in one table.

**Read what is on the chart**
> What are today's opening range high and low, the VWAP, and the fib pivot
> levels? Screenshot the chart.

**Debug a compile failure**
> Compile it and if there are errors, read them, fix the Pine, and recompile.
> Repeat until it compiles clean.

The agent can read compile errors (`pine_get_errors`), `log.info()` output
(`pine_get_console`), the Strategy Tester numbers (`data ... strategy`), and can
screenshot any of it — so it can close the loop itself rather than handing you
errors to paste back.

---

## 7. Before you trust any result

- **Calibrate to the contract first.** The defaults are scaled for MES. Put
  `ATR(14)` on your 5-minute chart, read a typical midday value, then set
  min stop ≈ 0.5 × ATR, max stop ≈ 4 × ATR, pivot buffer ≈ 0.3 × ATR. Leaving
  MES numbers on MNQ or MYM produces garbage, not a verdict.
- **Set real costs.** Strategy Settings → Properties: your commission per order
  and 1 tick of slippage. On a 1-micro strategy these are a material share of
  expectancy, not a rounding error.
- **Use the RTH session** so the prior-day fib pivots match the classic
  definition. On an ETH chart the pivots are computed off the overnight range too.
- **Check Total Closed Trades before anything else.** Under ~100, the numbers
  are noise. With one trade per day you get roughly 150–250 trades a year.
- **Read Profit Factor and Max Drawdown, not Net Profit.**
- **Test the same settings on MES, MNQ and MYM.** A filter that helps on all
  three is probably real. One that helps on one is probably curve-fitting.

---

## 8. Offline — no TradingView needed

The repo also carries a standalone backtest engine, so you can test the same
rules without TradingView at all:

```bash
npm run backtest -- --days 300 --detail
npm run backtest -- --csv data/MES_5min.csv --symbol MES --detail   # NinjaTrader export
npm run test:backtest
```

See `src/backtest/README.md`. Useful as a cross-check: if the Pine and the JS
engine disagree badly on the same data, one of them has a bug.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `CDP connection failed` | TradingView isn't running with `--remote-debugging-port=9222`. The normal shortcut doesn't count. |
| MCP server not listed in Claude Code | Check `.mcp.json` syntax and that the path is absolute. Restart Claude Code. |
| Windows "Access is denied" from WindowsApps | Use `tv_launch` (auto copy-fallback). Never `icacls` on WindowsApps. |
| Pine editor tools fail | Open the panel first: "open the pine editor panel". |
| Compile seems to do nothing | The button is "Add to chart" the first time and "Update on chart" after. `pine_smart_compile` handles both. |
| Strategy Tester empty | The script must be a `strategy()`, not an `indicator()`. |
| Tools return stale data | TradingView is still loading. Wait a few seconds and retry. |
| `tv` command not found | `npm link` from the repo folder, or use `node src/cli/index.js ...`. |

## Where things are

| Path | What |
|---|---|
| `pine/orb_vwap_pivot.pine` | The ORB + VWAP + fib-pivot strategy |
| `pine/README.md` | Its inputs, defaults and how to read the results |
| `scripts/setup_orb_layout.js` | One-command layout + load + compile + test |
| `src/backtest/` | Offline JS backtest engine (no TradingView needed) |
| `SETUP_GUIDE.md` | The generic MCP install guide this file expands on |
| `CLAUDE.md` | Which tool to use when — the agent reads this automatically |
