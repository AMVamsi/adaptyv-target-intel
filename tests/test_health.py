"""
The /health payload.

What's being pinned is the distinction between "down" and "degraded". A
server running in live-literature mode is still serving requests, but its
confidence numbers aren't calibrated - an operator should be able to see
that from outside without reading a verdict payload. Reporting that as `ok`
would hide it; reporting it as a crash would be wrong.
"""

from __future__ import annotations

from target_intel.engine import TargetIntelligenceEngine
from target_intel.mcp_server.health import get_health_status
from target_intel.mcp_server.server import TOOL_NAMES


def _status(mode: str) -> dict:
    engine = TargetIntelligenceEngine(mock=True, literature_mode=mode)
    try:
        return get_health_status(engine, TOOL_NAMES)
    finally:
        engine.close()


def test_demo_mode_is_ok():
    payload = _status("demo")
    assert payload["status"] == "ok"
    assert all(payload["checks"].values())


def test_payload_reports_which_modes_are_active():
    payload = _status("demo")
    assert payload["mode"] == {"foundry": "mock", "literature": "demo"}


def test_calibration_block_ships_its_sample_size():
    calibration = _status("demo")["calibration"]
    assert calibration["n_golden_examples"] == 14
    assert calibration["temperature"] > 0


def test_every_advertised_tool_appears():
    assert set(_status("demo")["tools"]) == set(TOOL_NAMES)


def test_live_literature_mode_is_degraded_not_ok():
    """Serving uncalibrated confidence is a state worth surfacing.

    Constructing the engine makes no network call - only fetching a target
    context would - so this stays offline.
    """
    payload = _status("live")
    assert payload["status"] == "degraded"
    assert payload["checks"]["confidence_calibrated"] is False
    assert payload["notes"]


def test_degraded_payload_explains_itself():
    assert "not calibrated probabilities" in " ".join(_status("live")["notes"])


def test_health_check_does_not_load_the_network_or_a_model():
    """A check polled every 30s must stay cheap; this asserts it completes
    without the live literature client ever being constructed."""
    import target_intel.literature.pubmed as pubmed

    calls = []
    original = pubmed.PubMedClient.__init__

    def _spy(self, *a, **kw):
        calls.append(1)
        return original(self, *a, **kw)

    pubmed.PubMedClient.__init__ = _spy
    try:
        _status("live")
    finally:
        pubmed.PubMedClient.__init__ = original
    assert calls == []
