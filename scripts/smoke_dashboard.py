#!/usr/bin/env python3
"""Pre-deploy smoke check for the sales dashboard. Fast, no browser.

Asserts the things that have silently regressed before:
  - pnl.js exists, is wired into index.html, and passes `node --check`.
  - index.html's inline script passes `node --check`.
  - key UI markers are present (day scrubber, leave toggle, delivery KPI, pnl.js tag).
  - STATE is a global (var STATE) so the extracted pnl.js can see it.
  - the pnl model conservation test passes (node scripts/test_pnl_model.mjs).

Exit 0 = ok, 1 = regression. Run before every dashboard deploy.
"""
import re, subprocess, sys, tempfile, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IDX = ROOT / "dashboard/sales/index.html"
PNL = ROOT / "dashboard/_shared/pnl.js"
problems = []

html = IDX.read_text()

# 1. pnl.js present + wired
if not PNL.exists():
    problems.append("dashboard/_shared/pnl.js is missing")
if 'src="/_shared/pnl.js"' not in html:
    problems.append("index.html does not load /_shared/pnl.js")

# 2. UI markers that have regressed before
MARKERS = {
    "day scrubber (tf-day)": "tf-day",
    "leave toggle": "toggleLeave",
    "delivery KPI (Mari)": "Delivery cost",
    "global STATE (var STATE)": "var STATE =",
}
for label, needle in MARKERS.items():
    if needle not in html:
        problems.append(f"missing UI/marker: {label} ('{needle}')")

# 3. node --check pnl.js and the inline script
def node_check(src_text, name):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(src_text); p = f.name
    r = subprocess.run(["node", "--check", p], capture_output=True, text=True)
    os.unlink(p)
    if r.returncode != 0:
        problems.append(f"node --check failed for {name}: {r.stderr.strip()[:200]}")

if PNL.exists():
    node_check(PNL.read_text(), "pnl.js")
blocks = re.findall(r'<script(?![^>]*\bsrc=)(?![^>]*type="module")[^>]*>([\s\S]*?)</script>', html)
if blocks:
    node_check(max(blocks, key=len), "index.html inline script")

# 4. model conservation test
test = ROOT / "scripts/test_pnl_model.mjs"
if test.exists():
    r = subprocess.run(["node", str(test)], capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        tail = (r.stdout + r.stderr).strip().splitlines()[-6:]
        problems.append("pnl model test FAILED:\n    " + "\n    ".join(tail))

if problems:
    print("DASHBOARD SMOKE CHECK FAILED:")
    for p in problems:
        print(f"  ✗ {p}")
    sys.exit(1)
print("dashboard smoke check: ok (pnl.js wired, markers present, syntax clean, model conserves)")
