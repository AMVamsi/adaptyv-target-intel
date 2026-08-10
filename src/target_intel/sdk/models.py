"""
Typed models for the Adaptyv Bio Foundry API.

Field names and shapes are kept faithful to the public API docs
(https://docs.adaptyvbio.com/api-reference/api-introduction) and the
example payloads shown on https://agents.adaptyvbio.com/, so that a
`mock=True` client and a `mock=False` client return identically-shaped
objects. This is deliberately narrow: it covers the resource groups this
project actually touches (targets, experiments, sequences, results) and
does not attempt to model quotes/invoices, which are out of scope here.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class ExperimentType(str, Enum):
    SCREENING = "screening"
    AFFINITY = "affinity"
    THERMOSTABILITY = "thermostability"
    FLUORESCENCE = "fluorescence"
    EXPRESSION = "expression"


class ExperimentStatus(str, Enum):
    DRAFT = "draft"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    QUOTE_SENT = "quote_sent"
    WAITING_FOR_MATERIALS = "waiting_for_materials"
    IN_QUEUE = "in_queue"
    IN_PRODUCTION = "in_production"
    DATA_ANALYSIS = "data_analysis"
    IN_REVIEW = "in_review"
    DONE = "done"
    CANCELED = "canceled"


class ResultsStatus(str, Enum):
    NONE = "none"
    PARTIAL = "partial"
    ALL = "all"


class BindingStrength(str, Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    NONE = "none"


class Target(BaseModel):
    """A target antigen from the Foundry catalog."""

    target_id: str
    name: str
    vendor: str | None = None
    selfservice_only: bool = False
    has_calibrated_price: bool = False
    # Neither field below is part of the real API response - they're
    # metadata this layer attaches itself, both inferable from the catalog
    # name in most entries (e.g. "HER2 / ERBB2"). They are separate on
    # purpose: `uniprot_hint` is the stable key literature claims are
    # cached under, while `gene_symbol` is what actually gets sent to
    # PubMed, because a literature search for an accession like "P04626"
    # returns essentially nothing - papers say "ERBB2".
    uniprot_hint: str | None = None
    gene_symbol: str | None = None


class ExperimentSpec(BaseModel):
    experiment_type: ExperimentType
    method: str | None = None  # e.g. "bli", "spr"
    target_id: str | None = None
    sequences: dict[str, str] = Field(default_factory=dict)
    n_replicates: int = 1


class CostBreakdown(BaseModel):
    """Normalised cost view.

    The live API nests this as `{type, breakdown: {assay, materials,
    total_cents}}`; the bundled fixtures were written flat. Both are accepted
    and end up here, so nothing downstream has to know which transport it
    came from - see `Experiment._normalise_api_shape`.
    """

    assay_cost_cents: int = 0
    material_cost_cents: int = 0
    total_cents: int = 0
    currency: str = "USD"
    pricing_version: str | None = None
    estimate: bool = True  # `costs.type == "estimate"` on the live response


class Experiment(BaseModel):
    """One Foundry experiment.

    `id`/`code` are accepted as aliases for `experiment_id`/`experiment_code`,
    which is what the live API returns. A captured response is saved at
    `tests/data/foundry_experiment_response.json`; it did not parse before
    these aliases existed, so the mock transport was the only one the models
    had ever been checked against.
    """

    model_config = ConfigDict(populate_by_name=True)

    experiment_id: str = Field(validation_alias=AliasChoices("experiment_id", "id"))
    experiment_code: str | None = Field(default=None, validation_alias=AliasChoices("experiment_code", "code"))
    name: str
    status: ExperimentStatus
    results_status: ResultsStatus = ResultsStatus.NONE
    experiment_spec: ExperimentSpec
    costs: CostBreakdown | None = None
    experiment_url: str | None = None
    webhook_url: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalise_api_shape(cls, data):
        """Flatten the live API's nested cost object into `CostBreakdown`.

        Done before validation rather than with a union type so that every
        consumer - dashboard, CLI, Neo4j loader - sees one shape regardless
        of transport. A union would push the branch onto every caller.
        """
        if not isinstance(data, dict):
            return data
        costs = data.get("costs")
        if isinstance(costs, dict) and "breakdown" in costs:
            breakdown = costs.get("breakdown") or {}
            data = {
                **data,
                "costs": {
                    "assay_cost_cents": (breakdown.get("assay") or {}).get("subtotal_cents", 0),
                    "material_cost_cents": (breakdown.get("materials") or {}).get("subtotal_cents", 0),
                    "total_cents": breakdown.get("total_cents", 0),
                    "pricing_version": breakdown.get("pricing_version"),
                    "estimate": costs.get("type") == "estimate",
                },
            }
        return data


class SequenceRecord(BaseModel):
    id: str
    sequence_id: str
    sequence_name: str
    target_id: str | None = None
    target_name: str | None = None
    experiment_id: str
    sequence: str | None = None


class ResultRecord(BaseModel):
    """A single sequence's experimental result within an experiment."""

    id: str
    sequence_id: str
    sequence_name: str
    target_id: str | None = None
    target_name: str | None = None
    experiment_id: str
    experiment_code: str | None = None

    # Affinity / screening fields
    kd: float | None = None
    kd_units: str | None = None
    kon: float | None = None
    koff: float | None = None
    binding_strength: BindingStrength | None = None
    r_squared: float | None = None

    # Thermostability
    tm_celsius: float | None = None

    # Expression
    expression_yield_mg_per_l: float | None = None

    n_replicates: int = 1
    is_control: bool = False


class ExperimentUpdate(BaseModel):
    """A single lifecycle update event (matches the webhook payload shape)."""

    update_id: str
    experiment_id: str
    name: str
    description: str
    update_type: str
    eta: datetime | None = None
    created_at: datetime
