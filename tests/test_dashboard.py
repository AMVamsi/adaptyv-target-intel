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
