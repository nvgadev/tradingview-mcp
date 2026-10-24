#!/usr/bin/env python3
"""Make research/gopi_research.pine = the live indicator + a trade-path logger (log.info).
Entries/exits are unchanged, so the trades are the same ones the live indicator takes."""
import os
HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "..", "pine", "gopi-hedge-x-pro.pine")).read()

# 1. result line when a trade closes (kind + R)
anchor = "    tradeUsd  = tradePts * syminfo.pointvalue - costPerTrade\n"
assert anchor in src
src = src.replace(anchor, anchor + '    rcRes := exitKind + ":" + str.tostring(tradeR, "#.##")\n', 1)
m5 = "// ---- 5. closed-trade accounting"
assert m5 in src
src = src.replace(m5, 'var string rcRes = ""\n' + m5, 1)

# 2. path logger, placed right before the webhook block
marker = "// ---- 6. ONE webhook alert per bar"
assert marker in src
block = '''// ====================== RESEARCH LOG (research copy only) ======================
// One recorder per entry (several can run at once): features at entry + the next rcMax bars of
// favourable / adverse excursion in R units, so any stop / target scheme can be re-evaluated
// offline on exactly the same entries, even if it holds longer than the live exit did.
rcMax = 48
htfEma60   = request.security(syminfo.tickerid, "60", ta.ema(close, 50)[1],  lookahead = barmerge.lookahead_on)
htfClose60 = request.security(syminfo.tickerid, "60", close[1],              lookahead = barmerge.lookahead_on)
htfEmaD    = request.security(syminfo.tickerid, "D",  ta.ema(close, 20)[1],  lookahead = barmerge.lookahead_on)
htfCloseD  = request.security(syminfo.tickerid, "D",  close[1],              lookahead = barmerge.lookahead_on)
var int[]    qBar  = array.new_int()
var int[]    qTid  = array.new_int()
var float[]  qEn   = array.new_float()
var float[]  qR    = array.new_float()
var int[]    qDir  = array.new_int()
var string[] qHead = array.new_string()
var string[] qFav  = array.new_string()
var string[] qAdv  = array.new_string()
var string[] qRes  = array.new_string()
if barstate.isconfirmed
    // advance every running recorder by one bar
    int qi = array.size(qBar) - 1
    while qi >= 0
        if bar_index > array.get(qBar, qi)
            float en = array.get(qEn, qi)
            float rr = array.get(qR, qi)
            int   dr = array.get(qDir, qi)
            int   nn = bar_index - array.get(qBar, qi)
            float fv = dr == 1 ? (h - en) / rr : (en - l) / rr
            float ad = dr == 1 ? (en - l) / rr : (h - en) / rr
            array.set(qFav, qi, array.get(qFav, qi) + (nn > 1 ? "," : "") + str.tostring(fv, "#.#"))
            array.set(qAdv, qi, array.get(qAdv, qi) + (nn > 1 ? "," : "") + str.tostring(ad, "#.#"))
            if nn >= rcMax
                label.new(bar_index, c, array.get(qHead, qi) + "|" + array.get(qRes, qi) + "|" + array.get(qFav, qi) + "|" + array.get(qAdv, qi), xloc = xloc.bar_index, yloc = yloc.price, color = color.new(color.white, 100), textcolor = color.new(color.white, 100), size = size.tiny)
                array.remove(qBar, qi)
                array.remove(qTid, qi)
                array.remove(qEn, qi)
                array.remove(qR, qi)
                array.remove(qDir, qi)
                array.remove(qHead, qi)
                array.remove(qFav, qi)
                array.remove(qAdv, qi)
                array.remove(qRes, qi)
        qi -= 1
    // the live trade just closed: attach its result to its recorder
    if not na(exitPx)
        int ri = array.indexof(qTid, entryTime)
        if ri >= 0
            array.set(qRes, ri, rcRes)
    // a new entry: start its recorder
    if entryEvent
        int  hAl60 = posDirTrade == 1 ? (htfClose60 > htfEma60 ? 1 : 0) : (htfClose60 < htfEma60 ? 1 : 0)
        int  hAlD  = posDirTrade == 1 ? (htfCloseD  > htfEmaD  ? 1 : 0) : (htfCloseD  < htfEmaD  ? 1 : 0)
        int  vwSd  = posDirTrade == 1 ? (c > vwapVal ? 1 : 0) : (c < vwapVal ? 1 : 0)
        array.push(qBar, bar_index)
        array.push(qTid, entryTime)
        array.push(qEn, entryPrice)
        array.push(qR, rDist)
        array.push(qDir, posDirTrade)
        array.push(qFav, "")
        array.push(qAdv, "")
        array.push(qRes, "")
        array.push(qHead, "TRD|" + str.tostring(entryTime) + "|" + str.tostring(posDirTrade) + "|" + str.tostring(entryPrice) + "|" + str.tostring(rDist, "#.##") + "|" + str.tostring(hour(time, "America/Chicago")) + "|" + str.tostring(minute(time, "America/Chicago")) + "|" + str.tostring(dayofweek(time, "America/Chicago")) + "|" + str.tostring(atrVal / c * 100, "#.###") + "|" + str.tostring(entryScore) + "|" + str.tostring(entryCount) + "|" + str.tostring(hAl60) + "|" + str.tostring(hAlD) + "|" + str.tostring(vwSd) + "|" + str.tostring(barsSinceRef))

'''
src = src.replace(marker, block + marker, 1)
out = os.path.join(HERE, "gopi_research.pine")
open(out, "w").write(src)
print("wrote", out, len(src.splitlines()), "lines")
