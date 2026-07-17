#!/usr/bin/env python3
"""
Assemble _site/ — the thing GitHub Pages actually serves.

    python3 scripts/build_site.py            # build
    python3 scripts/build_site.py --serve    # build + preview on :8000

WHY THIS EXISTS
---------------
The repo layout and the deployed layout are DIFFERENT, and until now nothing
let you see the deployed one locally. That cost a real bug: recipes.html asked
for '../data/ingredients.json', which works when you serve the repo root and
404s in production, because on Pages the page sits at _site/ and data at
_site/data/. It was never going to show up until a chef opened it.

So the layout is defined ONCE, here, and used by both the workflow and local
preview. If it works locally it works deployed, because it is the same code.

URLS ARE A CONTRACT
-------------------
/ must keep serving the main dashboard. People have it bookmarked.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "_site"

# The site map. One line per thing served. This IS the deployment.
#   (source, destination in _site)
LAYOUT: list[tuple[str, str]] = [
    ("dashboard",              ""),          # index.html, _shared/, users.json, logos -> /
    ("modules/recipes/app",    "recipes"),   # -> /recipes/
    ("data",                   "data"),      # the feeds -> /data/
    ("baselines",              "baselines"),
]


def build() -> int:
    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir(parents=True)

    # Rolling feeds are GENERATED at build time, never committed.
    #
    # data/ingredients.json is a 90-day window off date.today(). A committed
    # copy goes stale by the passage of time alone -- no code change, no commit,
    # just a Tuesday. Generating it here means the chef always sees a current
    # window and there is no stale-file class of bug at all.
    #
    # data/costs.csv is different: deterministic from cogs_list.csv, so it IS
    # committed and CI proves it reproduces. Two kinds of derived file; only one
    # of them can be checked byte-for-byte.
    for gen in ("modules/recipes/pipeline/build_ingredients.py",):
        r = subprocess.run([sys.executable, str(ROOT / gen)], capture_output=True, text=True, cwd=ROOT)
        if r.returncode:
            print(f"  FAILED {gen}\n{r.stderr}")
            return 1
        print(f"  generated via {gen.split('/')[-1]}")

    for src_rel, dst_rel in LAYOUT:
        src = ROOT / src_rel
        if not src.exists():
            print(f"  skip {src_rel} (not present)")
            continue
        dst = SITE / dst_rel if dst_rel else SITE
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            if item.name.startswith("."):
                continue
            target = dst / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        print(f"  {src_rel:<24} -> /{dst_rel}")

    return check()


_REF = re.compile(
    r"""(?:href|src)=["']([^"'#?]+)["']"""      # <link> <script> <img> <a>
    r"""|from\s+["'](\./[^"']+|\.\./[^"']+)["']"""   # es module imports
    r"""|Feed\.load\(\s*["']([^"'?]+)["']"""    # our shared feed loader
    r"""|fetch\(\s*["']([^"'?]+)["']"""         # hand-rolled fetches
)


def check() -> int:
    """
    Resolve EVERY local reference in every page and prove it exists in the build.

    The first version of this check was hand-written per-case and missed four
    real breakages in the first run: a page moved to /recipes/ and its
    './_shared/auth.js' silently became '/recipes/_shared/auth.js'. Specific
    checks only catch the bug you already thought of. So: don't guess — resolve
    the actual references.

    This is the whole reason the file exists. A broken path never fails a test,
    never fails a deploy, and shows up as a chef saying "the page is blank".
    """
    problems: list[str] = []
    site_root = SITE.resolve()

    if not (SITE / "index.html").exists():
        problems.append("no index.html at the site root — / would 404")

    for page in sorted(SITE.rglob("*.html")):
        text = page.read_text(errors="ignore")
        rel = page.relative_to(SITE)
        for m in _REF.finditer(text):
            ref = next(g for g in m.groups() if g)
            if ref.startswith(("http://", "https://", "//", "data:", "mailto:", "#")):
                continue
            # '/x' is site-root-relative and valid — we serve at a domain root
            # (dashboard/CNAME -> app.stowawaybar.com), not a /repo/ subpath.
            target = (SITE / ref.lstrip("/")).resolve() if ref.startswith("/") \
                     else (page.parent / ref).resolve()
            if not str(target).startswith(str(site_root)):
                problems.append(f"{rel}: {ref!r} escapes the site root")
            elif not target.exists():
                problems.append(f"{rel}: {ref!r} -> {target.relative_to(site_root)} does not exist")

    if problems:
        print("\nBUILD PROBLEMS — these 404 in production:")
        for p in problems:
            print(f"  ✗ {p}")
        return 1

    n = sum(1 for _ in SITE.rglob("*") if _.is_file())
    print(f"\n  ok — {n} files, every reference resolves")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true", help="preview on :8000 after building")
    args = ap.parse_args()

    rc = build()
    if rc:
        return rc
    if args.serve:
        import functools, http.server, socketserver
        h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
        print("\n  http://localhost:8000  (this is exactly what Pages serves)")
        with socketserver.TCPServer(("", 8000), h) as s:
            try:
                s.serve_forever()
            except KeyboardInterrupt:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
