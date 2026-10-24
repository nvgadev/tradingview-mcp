#!/usr/bin/env python3
"""
Deploy one GOPI input variant to the chart and read back the trade stats.

usage: gopi_variant.py LABEL key=value [key=value ...]
  webhookSecret=@secret bakes the receiver secret (secret.env) into the deployed copy only.
  keys are Pine input variable names (e.g. exitOnFlip=false useAtrExpFilter=true).
Never edits pine/gopi-hedge-x-pro.pine - works on a temp copy, so the repo file
keeps the chosen defaults.  Preconditions (checked): the Pine editor shows the
GOPI script (NOT another script) before anything is pushed or saved.
"""
import json, re, subprocess, sys, time, os, tempfile
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC  = os.environ.get("GOPI_SRC") or os.path.join(REPO, "pine", "gopi-hedge-x-pro.pine")
TV   = ["node", os.path.join(REPO, "src/cli/index.js")]
GOPI_ID = "USER;f05f19f930754f518677125b4a3d88a1"

def tv(*a, check=True):
    r = subprocess.run(TV + list(a), capture_output=True, text=True, cwd=REPO)
    try: return json.loads(r.stdout)
    except Exception: return {"_raw": r.stdout[-400:], "_err": r.stderr[-300:]}

def editor_title():
    r = tv("ui", "eval",
           "(()=>{const t=document.querySelector('[data-qa-id=\"pine-script-title-button\"]');return t?t.innerText.trim():null})()")
    return (r.get("result") or "").strip()

SECRET_ENV = os.path.expanduser("~/Library/Application Support/gopi-gc-paper/secret.env")

def read_secret():
    for ln in open(SECRET_ENV):
        if ln.startswith("WEBHOOK_SECRET="):
            return ln.split("=", 1)[1].strip()
    raise SystemExit("WEBHOOK_SECRET not found in " + SECRET_ENV)

def set_defaults(src, kv):
    kv = dict(kv)
    if kv.get("webhookSecret") == "@secret":      # baked into the CLOUD copy only, never the repo file
        kv["webhookSecret"] = '"%s"' % read_secret()
    for k, v in kv.items():
        pat = re.compile(r'^(%s\s*=\s*input\.(?:bool|int|float|string|session)\()([^,]+)(,)' % re.escape(k), re.M)
        if not pat.search(src): raise SystemExit(f"input {k} not found")
        val = v if v in ("true", "false") else v
        src = pat.sub(lambda m: m.group(1) + val + m.group(3), src, count=1)
    return src

ADD_BTN_JS = ("(()=>{const b=document.querySelector('[data-qa-id=\"add-script-to-chart\"]');"
              "if(!b)return null;const r=b.getBoundingClientRect();"
              "return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}})()")

def tables():
    r = tv("data", "tables", "--filter", "GOPI")
    out = {}
    for st in r.get("studies", []):
        for t in st.get("tables", []):
            for row in t["rows"]:
                if " | " in row:
                    k, v = row.split(" | ", 1); out[k.strip()] = v.strip()
    return out

def main():
    label, kv = sys.argv[1], dict(a.split("=", 1) for a in sys.argv[2:])
    title = editor_title()
    if title != "GOPI HEDGE X PRO":
        raise SystemExit(f"REFUSING: editor shows '{title}', not GOPI HEDGE X PRO")
    src = set_defaults(open(SRC).read(), kv)
    tmp = os.path.join(tempfile.gettempdir(), "gopi_variant.pine"); open(tmp, "w").write(src)
    shown = {k: ("<secret>" if "secret" in k.lower() else v) for k, v in kv.items()}
    print(label, shown, "| set:", tv("pine", "set", "--file", tmp).get("lines_set"))
    c = tv("pine", "compile")
    if c.get("has_errors"): raise SystemExit(f"compile errors: {c}")
    tv("pine", "save"); time.sleep(3); tv("pine", "save"); time.sleep(3)
    ids = [s["id"] for s in tv("state").get("studies", []) if s["name"] == "GOPI HEDGE X PRO"]
    for i in ids: tv("indicator", "remove", i)
    time.sleep(2)
    # Pine editor header "Add to chart" - looked up by its stable data-qa-id, because its
    # x position moves with the editor width (880 once, 826 later).
    pos = None
    for _ in range(12):                 # the button is replaced by a spinner while the editor saves
        pos = tv("ui", "eval", ADD_BTN_JS).get("result")
        if pos: break
        time.sleep(3)
    if not pos: raise SystemExit("Add-to-chart button not found - is the Pine editor open?")
    tv("ui", "mouse", str(pos["x"]), str(pos["y"]))
    t = {}
    for _ in range(12):                 # wait for the study to compute (up to ~60s)
        time.sleep(5)
        t = tables()
        if t.get("TRADES"): break
    if not t.get("TRADES"): raise SystemExit("GOPI stats table did not appear - instance not on chart?")
    keep = ["TRADES","LOST (stop-loss hit)","SMALL WIN (stop moved up)","BIG WIN (final target)",
            "TOTAL RESULT","AVERAGE PER TRADE","BIGGEST DROP","WORST LOSING STREAK","PERIOD"]
    print(json.dumps({k: t.get(k) for k in keep}, indent=0))

main()
