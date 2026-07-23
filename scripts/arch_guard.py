#!/usr/bin/env python3
"""Architecture guard for the sales dashboard — the guardrail that makes drift
IMPOSSIBLE, not just documented. Fails the build if business logic creeps back
into index.html or a module breaks the layering. Wire into CI + every deploy.

Enforced invariants:
  R1  index.html carries NO business logic — zero top-level function declarations
      in its inline script. All logic lives in /_shared/*.js modules.
  R2  index.html stays a shell — file size under the cap (logic creeping back
      shows up as growth).
  R3  the logic modules exist:      pnl.js util.js data.js render.js
  R4  every module + the inline script passes `node --check`.
  R5  pnl.js is PURE — no DOM / rendering tokens (the model never touches the page).
  R6  no logic function is defined twice across the modules.
  R7  the UI/behaviour markers exist in the bundle (day scrubber, leave toggle,
      Mari delivery KPI, global STATE, all module <script> tags).
  R8  the P&L model conservation test passes.

Exit 0 = ok, 1 = architecture regression. This is what stops a future edit
(mine or anyone's) from bolting logic onto the HTML again.
"""
import re, subprocess, sys, tempfile, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SH = ROOT / "dashboard/_shared"
IDX = ROOT / "dashboard/sales/index.html"
MODULES = ["pnl.js", "util.js", "data.js", "render.js"]
SIZE_CAP = 100 * 1024
FN_DECL = re.compile(r'(?m)^\s*function\s+([A-Za-z0-9_]+)\s*\(')
DOM_TOKENS = re.compile(r'document\.|getElementById|innerHTML|addEventListener|querySelector|\.style\b|location\.|\bwindow\.|\.classList|\.textContent')
problems = []

def strip_comments(src):
    # remove /* */ and // comments so prose like "cover the window." never trips
    # the DOM-token or function-declaration checks. Good enough for guard purposes.
    src = re.sub(r'/\*[\s\S]*?\*/', '', src)
    src = re.sub(r'(?m)//.*$', '', src)
    return src

html = IDX.read_text()
def inline_script(h):
    blocks = re.findall(r'<script(?![^>]*\bsrc=)(?![^>]*type="module")[^>]*>([\s\S]*?)</script>', h)
    return max(blocks, key=len) if blocks else ""
inline = strip_comments(inline_script(html))

# R1 — no function declarations in the HTML
idx_fns = FN_DECL.findall(inline)
if idx_fns:
    problems.append(f"R1: index.html inline script defines {len(idx_fns)} function(s) — logic must live in modules: {', '.join(idx_fns[:8])}")

# R2 — size cap
sz = IDX.stat().st_size
if sz > SIZE_CAP:
    problems.append(f"R2: index.html is {sz//1024}KB, over the {SIZE_CAP//1024}KB shell cap — logic likely creeping back")

# R3/R4/R5/R6 — modules
def node_check(text, name):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(text); p = f.name
    r = subprocess.run(["node", "--check", p], capture_output=True, text=True); os.unlink(p)
    if r.returncode:
        problems.append(f"R4: node --check failed for {name}: {r.stderr.strip()[:160]}")

defined = {}
for mod in MODULES:
    p = SH / mod
    if not p.exists():
        problems.append(f"R3: required module missing: {mod}"); continue
    txt = p.read_text()
    node_check(txt, mod)
    for fn in FN_DECL.findall(strip_comments(txt)):
        defined.setdefault(fn, []).append(mod)
node_check(inline, "index.html inline")

# R5 — pnl.js purity
pnl = SH / "pnl.js"
pnl_code = strip_comments(pnl.read_text()) if pnl.exists() else ""
if pnl.exists() and DOM_TOKENS.search(pnl_code):
    tok = DOM_TOKENS.search(pnl_code).group(0)
    problems.append(f"R5: pnl.js is impure — contains DOM token '{tok}'. The model must not touch the page.")

# R6 — no duplicate logic
for fn, where in defined.items():
    if len(where) > 1:
        problems.append(f"R6: function '{fn}' defined in multiple modules: {', '.join(where)}")

# R7 — behaviour markers somewhere in the bundle
bundle = html + "".join((SH / m).read_text() for m in MODULES if (SH / m).exists())
MARKERS = {"day scrubber": "tf-day", "leave toggle": "toggleLeave",
           "Mari delivery KPI": "Delivery cost", "global STATE": "var STATE =",
           "pnl.js tag": 'src="/_shared/pnl.js"', "util.js tag": 'src="/_shared/util.js"',
           "data.js tag": 'src="/_shared/data.js"', "render.js tag": 'src="/_shared/render.js"'}
for label, needle in MARKERS.items():
    if needle not in bundle:
        problems.append(f"R7: missing marker '{label}' ('{needle}')")

# R8 — model conservation
test = ROOT / "scripts/test_pnl_model.mjs"
if test.exists():
    r = subprocess.run(["node", str(test)], capture_output=True, text=True, cwd=ROOT)
    if r.returncode:
        tail = (r.stdout + r.stderr).strip().splitlines()[-5:]
        problems.append("R8: pnl model test FAILED:\n    " + "\n    ".join(tail))

if problems:
    print("ARCHITECTURE GUARD FAILED — the dashboard drifted from its module structure:")
    for p in problems:
        print(f"  ✗ {p}")
    print("\nLogic belongs in dashboard/_shared/{pnl,util,data,render}.js — never in index.html.")
    sys.exit(1)
print(f"architecture guard: ok — index.html is a {sz//1024}KB shell, 0 logic fns; "
      f"{len(defined)} module fns, pnl.js pure, model conserves.")
