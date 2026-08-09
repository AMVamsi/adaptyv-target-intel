"""
AdaptyvClient: one typed client, backed by either MockTransport or
LiveTransport. Every method returns the same pydantic models regardless
of which transport is behind it.

    client = AdaptyvClient(mock=True)                       # fixtures
    client = AdaptyvClient(mock=False, api_token="...")      # real API

This is the "clean, installable SDK" half of the JD's ask
("Wrapping our internal APIs ... into clean, installable SDKs and MCP
servers so agents and teammates can plug into them in minutes"), scoped
here to Adaptyv's already-public Foundry API as a stand-in for the
internal LabOS APIs this role would actually wrap.
"""

from __future__ import annotations

import os

from .models import Experiment, ResultRecord, Target
from .transport import LiveTransport, MockTransport, Transport


class AdaptyvClient:
    def __init__(
        self,
        mock: bool = True,
        api_token: str | None = None,
        base_url: str | None = None,
    ):
        self.mock = mock
        if mock:
            self._transport: Transport = MockTransport()
        else:
            token = api_token or os.environ.get("FOUNDRY_API_TOKEN")
            if not token:
                raise ValueError(
                    "api_token is required when mock=False (or set FOUNDRY_API_TOKEN)"
                )
            kwargs = {"api_token": token}
            if base_url:
                kwargs["base_url"] = base_url
            self._transport = LiveTransport(**kwargs)

    # ---- targets ----------------------------------------------------

    def list_targets(self, search: str | None = None) -> list[Target]:
        params = {"search": search} if search else {}
        data = self._transport.get("/targets", params=params)
        return [Target.model_validate(t) for t in data["items"]]

    def get_target(self, target_id: str) -> Target:
        data = self._transport.get(f"/targets/{target_id}")
        return Target.model_validate(data)

    # ---- experiments --------------------------------------------------

    def list_experiments(self, status: str | None = None) -> list[Experiment]:
        params = {"status": status} if status else {}
        data = self._transport.get("/experiments", params=params)
        return [Experiment.model_validate(e) for e in data["items"]]

    def get_experiment(self, experiment_id: str) -> Experiment:
        data = self._transport.get(f"/experiments/{experiment_id}")
        return Experiment.model_validate(data)

    # ---- results --------------------------------------------------------

    def get_results(self, experiment_id: str) -> list[ResultRecord]:
        data = self._transport.get(f"/experiments/{experiment_id}/results")
        return [ResultRecord.model_validate(r) for r in data["items"]]

    def list_results_for_target(self, target_id: str) -> list[ResultRecord]:
        """Not a real Foundry endpoint as such (results are nested under an
        experiment in the real API) - implemented here client-side by
        listing done experiments for the target and merging their results,
        since the interpretation layer needs a target-centric view."""
        results: list[ResultRecord] = []
        for exp in self.list_experiments():
            if exp.experiment_spec.target_id == target_id and exp.results_status.value != "none":
                results.extend(self.get_results(exp.experiment_id))
        return results

    def close(self) -> None:
        close = getattr(self._transport, "close", None)
        if close:
            close()
