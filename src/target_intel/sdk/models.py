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

from pydantic import BaseModel, Field


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


class CostBreakdown(BaseModel):
    assay_cost_cents: int = 0
    material_cost_cents: int = 0
    total_cents: int = 0
    currency: str = "USD"


class Experiment(BaseModel):
    experiment_id: str
    experiment_code: str | None = None
    name: str
    status: ExperimentStatus
    results_status: ResultsStatus = ResultsStatus.NONE
    experiment_spec: ExperimentSpec
    costs: CostBreakdown | None = None
    webhook_url: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


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
