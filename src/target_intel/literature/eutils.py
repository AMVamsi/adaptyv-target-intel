"""
Shared transport for NCBI E-utilities.

Both the literature client (`pubmed.py`) and the gene/protein resolver
(`ncbi.py`) hit the same API under the same rules, so the throttling,
retry, and identification behaviour lives here once rather than being
copied and then drifting.

Two things this enforces, both of which are easy to get wrong quietly:

  - **Rate limiting.** NCBI documents 3 requests/second anonymously and 10
    with an API key. Exceeding it gets you throttled, not errored, so the
    symptom is intermittent slowness rather than an obvious failure.
  - **Retries.** E-utilities returns a transient 400/429/5xx under burst
    load even for well-formed requests - observed repeatedly while
    developing against it. One failed call is not evidence of a bad query,
    so a bounded retry with backoff prevents a spurious "no results".
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# NCBI's documented ceilings.
RATE_LIMIT_NO_KEY_S = 0.34
RATE_LIMIT_WITH_KEY_S = 0.11


class EUtilsClient:
    """Throttled, retrying, self-identifying E-utilities transport."""

    #: Subclasses set the default `db` sent with every request.
    default_db: str = ""

    def __init__(
        self,
        tool_name: str = "adaptyv-target-intel",
        email: str | None = None,
        api_key: str | None = None,
        rate_limit_s: float | None = None,
        timeout: float = 20.0,
    ):
        self._tool = tool_name
        self._email = email or os.environ.get("NCBI_EMAIL")
        self._api_key = api_key or os.environ.get("NCBI_API_KEY")
        self._rate_limit_s = rate_limit_s if rate_limit_s is not None else (
            RATE_LIMIT_WITH_KEY_S if self._api_key else RATE_LIMIT_NO_KEY_S
        )
        self._client = httpx.Client(base_url=EUTILS_BASE, timeout=timeout)
        self._last_call = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self._rate_limit_s:
            time.sleep(self._rate_limit_s - elapsed)
        self._last_call = time.monotonic()

    def _common_params(self, db: str | None = None) -> dict[str, str]:
        """NCBI asks non-interactive callers to identify themselves; an
        API key also raises the rate ceiling."""
        params = {"db": db or self.default_db, "tool": self._tool}
        if self._email:
            params["email"] = self._email
        if self._api_key:
            params["api_key"] = self._api_key
        return params

    def _request(self, method: str, path: str, payload: dict, attempts: int = 3) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(attempts):
            self._throttle()
            try:
                if method == "POST":
                    resp = self._client.post(path, data=payload)
                else:
                    resp = self._client.get(path, params=payload)
                resp.raise_for_status()
                return resp
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(self._rate_limit_s * (2**attempt))
        raise RuntimeError(f"E-utilities {path} failed after {attempts} attempts") from last_error

    def _get_json(self, path: str, payload: dict) -> Any:
        return self._request("GET", path, {**payload, "retmode": "json"}).json()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> EUtilsClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
