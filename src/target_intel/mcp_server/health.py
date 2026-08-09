"""
Health payload for the `/health` endpoint.

Ported from the health check in the MSc thesis pipeline
(`src/mcp_server/health.py`), which backs a Docker Compose healthcheck
polled every 30s. The design constraint carries over unchanged: a health
check must be **cheap**. It reports readiness from metadata and
already-loaded state, and never triggers model loading, a literature fetch
or a network call - otherwise polling it every 30s becomes a self-inflicted
load problem, and a slow health check reads as an outage.

The distinction between `ok` and `degraded` is deliberate. A server whose
literature layer is in live mode and whose confidence is therefore
uncalibrated is still *serving* - it is not down - but an operator should
be able to see that from the outside without reading verdict payloads.
"""

from __future__ import annotations

from typing import Any

from ..engine import TargetIntelligenceEngine
from ..interpretation import CalibrationStatus


def get_health_status(engine: TargetIntelligenceEngine, tools: list[str]) -> dict[str, Any]:
    """Build the JSON payload served at /health.

    Cheap by construction: the calibrator is already fit at engine
    construction, the tool list is registry metadata, and the fixture count
    is a length. Nothing here touches the network or loads a model.
    """
    calibrated = engine.calibration_status is CalibrationStatus.CALIBRATED

    checks = {
        "calibrator_fitted": engine.calibrator.temperature > 0,
        "fixtures_loaded": len(engine.client.list_targets()) > 0,
        "confidence_calibrated": calibrated,
    }

    # A failed *check* is degraded, not down - the server still answers, and
    # an operator needs to be able to tell those apart.
    status = "ok" if all(checks.values()) else "degraded"

    payload: dict[str, Any] = {
        "status": status,
        "service": "adaptyv-target-intel",
        "version": _version(),
        "mode": {
            "foundry": "mock" if engine.client.mock else "live",
            "literature": engine.literature_mode,
        },
        "calibration": {
            "status": engine.calibration_status.value,
            "temperature": round(engine.calibrator.temperature, 4),
            "n_golden_examples": len(engine.golden_claims),
        },
        "tools": sorted(tools),
        "checks": checks,
    }

    if not calibrated:
        payload["notes"] = [
            "Literature layer is in live mode; confidence scores are an ordering "
            "signal, not calibrated probabilities. See CalibrationStatus."
        ]

    return payload


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("target-intel")
    except Exception:  # pragma: no cover - running from a source tree
        return "0.1.0+source"
