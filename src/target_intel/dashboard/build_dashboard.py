"""
Builds the final, self-contained dashboard.html by embedding the portfolio
report JSON directly into the HTML template. Deliberately not using
`fetch('./portfolio_report.json')` at runtime - browsers block local-file
fetch/XHR by default (CORS), and the whole point of this dashboard is
that it opens with a double-click, zero server, zero setup.

Usage:
    python -m target_intel.dashboard.build_dashboard
"""

from __future__ import annotations

import json
from pathlib import Path

from . import generate_report

HERE = Path(__file__).parent
TEMPLATE_PATH = HERE / "template.html"
OUTPUT_PATH = HERE / "dashboard.html"

# The GitHub Pages copy. `docs/index.html` is committed and served at
# https://amvamsi.github.io/adaptyv-target-intel/ so the dashboard can be
# looked at without cloning anything - a reviewer gets the output in one
# click. It is written by the same build as the local copy rather than
# copied by hand, because a published artifact that drifts from the code is
# worse than no published artifact: CI rebuilds and fails if the two differ.
PAGES_PATH = HERE.parent.parent.parent / "docs" / "index.html"


def build(publish: bool = False) -> Path:
    """Write the self-contained dashboard and return its path.

    `publish` also refreshes the committed GitHub Pages copy. It is opt-in
    rather than automatic because writing into `docs/` is a side effect a
    caller should ask for: the freshness test compares the committed copy
    against a fresh build, and a build that silently rewrote it first would
    be comparing the file to itself and could never fail.
    """
    report = generate_report.run()
    template = TEMPLATE_PATH.read_text()
    html = template.replace("__PORTFOLIO_DATA__", json.dumps(report, default=str))
    OUTPUT_PATH.write_text(html)
    if publish and PAGES_PATH.parent.is_dir():  # docs/ is absent in an installed package
        PAGES_PATH.write_text(html)
    return OUTPUT_PATH


if __name__ == "__main__":
    path = build(publish=True)
    print(f"Wrote {path}")
    if PAGES_PATH.exists():
        print(f"Wrote {PAGES_PATH}  (GitHub Pages copy)")
