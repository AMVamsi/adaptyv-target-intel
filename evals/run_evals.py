"""
Thin CLI wrapper over `target_intel.evals`.

The logic itself lives in the package (see the docstring there) so that
`target-intel eval` keeps working after `pip install`, where this directory
isn't shipped. This script exists so the eval suite is also runnable the
obvious way, straight from a clone, with no install:

    python evals/run_evals.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from target_intel.evals import format_report, run, run_deterministic_guards  # noqa: E402

if __name__ == "__main__":
    report = run()
    failures = run_deterministic_guards(report)
    print(format_report(report, failures))

    out_path = Path(__file__).parent / "eval_report.json"
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nWrote {out_path}")

    if failures:
        sys.exit(1)
