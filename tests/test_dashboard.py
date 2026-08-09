from target_intel.dashboard import build_dashboard, generate_report


def test_generate_report_covers_all_targets_and_experiments():
    report = generate_report.run()
    assert len(report["targets"]) == 5
    assert len(report["experiments"]) == 5
    assert "calibration" in report
    her2 = next(t for t in report["targets"] if t["target_id"] == "comp-her2-human")
    assert "trastuzumab" in her2["known_binders"]


def test_build_dashboard_embeds_data_with_no_leftover_placeholder():
    path = build_dashboard.build()
    html = path.read_text()
    assert "__PORTFOLIO_DATA__" not in html
    assert "</script" not in html.split("const DATA")[0]  # sanity: no injection before data
    assert "generated_at" in html
    assert path.exists()


def test_published_pages_dashboard_matches_a_fresh_build():
    """`docs/index.html` is served by GitHub Pages, so it's the copy people
    see without cloning. A published artifact that has drifted from the code
    is worse than none at all - it shows a reviewer output the repo no longer
    produces. The stale committed screenshot was exactly this bug once.

    `generated_at` is excluded: it changes on every build by design, and
    comparing it would make this fail for the one reason that doesn't matter.
    """
    import json
    import re
    from pathlib import Path

    pages = Path(__file__).resolve().parent.parent / "docs" / "index.html"
    assert pages.exists(), "run: python -m target_intel.dashboard.build_dashboard"

    def payload(html: str) -> dict:
        match = re.search(r"const DATA = (\{.*\});", html, re.S)
        assert match, "embedded portfolio data not found in the page"
        data = json.loads(match.group(1))
        data.pop("generated_at", None)
        return data

    # Read the committed copy BEFORE building: build() rewrites this very
    # file, so building first would compare it against itself and pass no
    # matter how stale it was.
    published = payload(pages.read_text())
    fresh = payload(build_dashboard.build().read_text())

    assert published == fresh, (
        "docs/index.html is out of date - rebuild it with "
        "`python -m target_intel.dashboard.build_dashboard` and commit the result"
    )
