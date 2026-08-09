"""
Coverage / gap analysis - the "reasoning about absence" idea generalized
from the GDKG concept's "missing keystone taxa" pattern, re-derived from
scratch for protein targets rather than reused from anywhere.

Honest scope, stated up front: a Foundry affinity/screening result tells
you a sequence binds with some KD - it does NOT tell you *which epitope*
it binds, unless a separate epitope-binning/competition assay was run.
So this module does not (and cannot honestly) claim "your campaign hit
epitope A but missed epitope B." What it CAN honestly claim, purely from
what the literature-grounding layer already knows, is narrower and still
useful: "this target has multiple, independently-documented epitope
classes - a single-assay-type campaign against it may not tell you which
epitope(s) your candidates actually engage, and that's worth checking
before assuming full coverage."

This is a portfolio-level view, not a per-sequence one, and it's meant to
answer the JD's "pulling together experiment ... data to surface insights
the team would otherwise miss" bullet at the campaign-planning level.
"""

from __future__ import annotations

from dataclasses import dataclass

from .prior_model import ExpectedAffinityPrior

MULTI_EPITOPE_THRESHOLD = 2


@dataclass
class CoverageNote:
    target_id: str
    target_name: str
    known_epitopes: list[str]
    note: str


def epitope_diversity_note(target_id: str, target_name: str, prior: ExpectedAffinityPrior) -> CoverageNote | None:
    """Returns a coverage note if the literature documents multiple,
    independent epitope classes for this target - a signal that a single
    binding/screening assay may not confirm which epitope(s) a campaign's
    candidates actually engage. Returns None for targets with 0 or 1 known
    epitopes, where this concern doesn't apply."""
    epitopes = prior.known_epitopes
    if len(epitopes) < MULTI_EPITOPE_THRESHOLD:
        return None

    epitope_list = ", ".join(epitopes)
    note = (
        f"{target_name}: literature documents {len(epitopes)} independent epitope "
        f"classes ({epitope_list}), each with its own validated binders. A standard "
        "affinity/screening assay confirms binding but not which epitope is engaged - "
        "recommend an epitope-binning or competition assay against a known reference "
        "binder for each class before treating this campaign as having explored the "
        "full known epitope space for this target."
    )
    return CoverageNote(
        target_id=target_id,
        target_name=target_name,
        known_epitopes=epitope_list.split(", "),
        note=note,
    )
