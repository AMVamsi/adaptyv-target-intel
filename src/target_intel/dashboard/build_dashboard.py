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


def build() -> Path:
    report = generate_report.run()
    template = TEMPLATE_PATH.read_text()
    html = template.replace("__PORTFOLIO_DATA__", json.dumps(report, default=str))
    OUTPUT_PATH.write_text(html)
    return OUTPUT_PATH


if __name__ == "__main__":
    path = build()
    print(f"Wrote {path}")
