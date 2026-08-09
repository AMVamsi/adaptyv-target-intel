"""
Transport layer for the Foundry API client.

Two implementations share one interface:

- `MockTransport` reads from the JSON fixtures in `sdk/fixtures/`, which are
  shaped to match the real API responses documented at
  https://docs.adaptyvbio.com/api-reference/api-introduction (five resource
  groups: targets, experiments, sequences, results; same field names).
- `LiveTransport` calls the real Foundry API (`devs.adaptyvbio.com` /
  `foundry-api-public.adaptyvbio.com`) with a bearer token.

`AdaptyvClient` (in client.py) is written once against this interface and
does not know or care which transport it's holding — exactly the "one
client, mock=True or mock=False, identical shapes either way" pattern the
JD asks for ("wrap our internal APIs into clean, installable SDKs").

No live network calls are exercised by this project's own test suite -
LiveTransport is here so the client is a genuine drop-in for a real
account, but every test and every demo run in this repo goes through
MockTransport by default.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

import httpx

FIXTURES_DIR = Path(__file__).parent / "fixtures"

DEFAULT_BASE_URL = "https://devs.adaptyvbio.com/api/v1"


class Transport(Protocol):
    def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...
    def post(self, path: str, json_body: dict[str, Any] | None = None) -> Any: ...
    def patch(self, path: str, json_body: dict[str, Any] | None = None) -> Any: ...


class FoundryAPIError(RuntimeError):
    """Raised for any non-2xx response from the Foundry API (live mode)."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Foundry API error {status_code}: {detail}")


class MockTransport:
    """Serves fixture data. Deliberately does not implement pagination or
    server-side filtering (the real API's `filter=`/`sort=` s-expression
    syntax) - fixtures always return the full set and callers filter
    client-side. This mirrors a known, explicitly-declared limitation
    rather than silently pretending to support it."""

    def __init__(self, fixtures_dir: Path = FIXTURES_DIR):
        self._targets = json.loads((fixtures_dir / "targets.json").read_text())
        self._experiments = json.loads((fixtures_dir / "experiments.json").read_text())
        self._results = json.loads((fixtures_dir / "results.json").read_text())

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        if path == "/targets":
            items = list(self._targets)
            search = params.get("search")
            if search:
                items = [t for t in items if search.lower() in t["name"].lower()]
            return {"items": items, "total": len(items)}

        if path.startswith("/targets/"):
            target_id = path.split("/")[-1]
            for t in self._targets:
                if t["target_id"] == target_id:
                    return t
            raise FoundryAPIError(404, f"target {target_id} not found")

        if path == "/experiments":
            items = list(self._experiments)
            status = params.get("status")
            if status:
                items = [e for e in items if e["status"] == status]
            return {"items": items, "total": len(items)}

        if path.startswith("/experiments/") and path.endswith("/results"):
            experiment_id = path.split("/")[-2]
            items = [r for r in self._results if r["experiment_id"] == experiment_id]
            return {"items": items, "total": len(items)}

        if path.startswith("/experiments/"):
            experiment_id = path.split("/")[-1]
            for e in self._experiments:
                if e["experiment_id"] == experiment_id:
                    return e
            raise FoundryAPIError(404, f"experiment {experiment_id} not found")

        if path == "/results":
            items = list(self._results)
            target_id = params.get("target_id")
            if target_id:
                items = [r for r in items if r.get("target_id") == target_id]
            return {"items": items, "total": len(items)}

        raise FoundryAPIError(404, f"no mock route for GET {path}")

    def post(self, path: str, json_body: dict[str, Any] | None = None) -> Any:
        raise NotImplementedError(
            "MockTransport is read-only in this project. Creating/submitting "
            "experiments against a real org is out of scope for a take-home - "
            "this project focuses on interpreting results that already exist."
        )

    def patch(self, path: str, json_body: dict[str, Any] | None = None) -> Any:
        raise NotImplementedError("MockTransport is read-only in this project.")


class LiveTransport:
    """Thin httpx wrapper over the real Foundry API. Requires a bearer token.

    Kept intentionally small: this project's contribution is the
    interpretation layer built on top, not re-implementing Adaptyv's own
    (already excellent) SDK and MCP server. See README for why this repo
    doesn't try to out-build those.
    """

    def __init__(self, api_token: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 30.0):
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=timeout,
        )

    def _handle(self, resp: httpx.Response) -> Any:
        if resp.status_code >= 400:
            raise FoundryAPIError(resp.status_code, resp.text)
        return resp.json()

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._handle(self._client.get(path, params=params))

    def post(self, path: str, json_body: dict[str, Any] | None = None) -> Any:
        return self._handle(self._client.post(path, json=json_body))

    def patch(self, path: str, json_body: dict[str, Any] | None = None) -> Any:
        return self._handle(self._client.patch(path, json=json_body))

    def close(self) -> None:
        self._client.close()
