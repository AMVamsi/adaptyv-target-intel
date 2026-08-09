"""
The payoff of the whole pipeline: given one experimental result and its
target's literature prior, produce a structured, cited verdict a scientist
would actually want to read - distinguishing "expected hit", "novel
candidate worth a second look", and "numbers look biologically
implausible, check for an assay artifact before this reaches a customer".

This is the thing Adaptyv's own deterministic anomaly rules (numeric QC
range checks on controls) and a plain LLM summary of the result would
both miss: literature-grounded plausibility, with provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..literature.relation_extraction import EvidenceLevel
from ..sdk.models import ResultRecord
from .prior_model import CalibrationStatus, ExpectedAffinityPrior, LiteratureDensity

_UNCALIBRATED_SUFFIX = (
    " NOTE: this run used live PubMed retrieval, whose scores fall outside the "
    "distribution the confidence calibrator was fit on - read the confidence "
    "figure above as an ordering signal only, not as a probability."
)

TIGHTER_THAN_EXPECTED_MULTIPLE = 5.0  # "N x tighter than the best literature precedent"
WEAKER_THAN_EXPECTED_MULTIPLE = 5.0


class VerdictLabel(str, Enum):
    NO_BINDING = "no_binding"
    CONSISTENT_WITH_LITERATURE = "consistent_with_literature"
    NOVEL_CANDIDATE = "novel_candidate"
    OUTSIDE_KNOWN_RANGE_FLAG = "outside_known_range_flag_artifact"
    WEAKER_THAN_TYPICAL = "weaker_than_typical"
    QUALITATIVE_LITERATURE_ONLY = "qualitative_literature_only"
    LITERATURE_CONFLICT = "literature_conflict"


@dataclass
class Verdict:
    label: VerdictLabel
    flag_for_review: bool
    confidence: float
    rationale: str
    citations: list[str]


def _fmt_m(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x:.2e} M"


def interpret_result(result: ResultRecord, prior: ExpectedAffinityPrior) -> Verdict:
    """Public entrypoint. Delegates to the rule cascade, then stamps the
    rationale when the confidence figure isn't one the calibrator can
    vouch for - so the caveat travels with the text a scientist reads,
    not just with a field they might not look at."""
    verdict = _interpret(result, prior)
    if prior.calibration_status is CalibrationStatus.UNCALIBRATED_LIVE:
        verdict.rationale += _UNCALIBRATED_SUFFIX
    return verdict


def _conflict_verdict(result: ResultRecord, kd: float, prior: ExpectedAffinityPrior) -> Verdict:
    """Sources disagree AND the result sits outside the consensus core -
    so which source you trust actually changes the answer. Reserved for
    that case: on real literature almost every target has some
    disagreement, and returning this for results that land squarely
    inside the consensus made it swallow every other verdict."""
    ranges_desc = "; ".join(
        f"{pmid} reports {_fmt_m(lo)}-{_fmt_m(hi)}" for pmid, lo, hi in prior.conflicting_source_ranges
    )
    return Verdict(
        label=VerdictLabel.LITERATURE_CONFLICT,
        flag_for_review=True,
        confidence=prior.calibrated_confidence,
        rationale=(
            f"{result.sequence_name}: binding detected against {result.target_name} "
            f"(KD = {_fmt_m(kd)}), but the literature sources found for this target "
            f"disagree with each other rather than converging on one range ({ranges_desc}). "
            "This is common when sources used different binding formats (e.g. bivalent IgG "
            "vs. monovalent nanobody) that aren't directly comparable. Declining to call this "
            "result consistent or inconsistent against a blended range would overstate what's "
            "actually known - recommend checking which binding format/context this result's "
            "comparison should use before drawing a conclusion."
        ),
        citations=[pmid for pmid, _, _ in prior.conflicting_source_ranges],
    )



def _interpret(result: ResultRecord, prior: ExpectedAffinityPrior) -> Verdict:
    citations = prior.pmids

    no_binding = (
        result.kd is None
        or (result.binding_strength is not None and result.binding_strength.value in ("none", "weak"))
    )
    if no_binding:
        return Verdict(
            label=VerdictLabel.NO_BINDING,
            flag_for_review=False,
            confidence=prior.calibrated_confidence,
            rationale=(
                f"{result.sequence_name}: no meaningful binding detected against {result.target_name}. "
                "This is the expected outcome for most designs in a screen and doesn't warrant a "
                "literature-consistency check."
            ),
            citations=[],
        )

    # The no_binding branch above already returned for kd is None; this
    # rebinding is what lets a type checker see that too.
    kd: float = result.kd  # type: ignore[assignment]

    # Checked before the conflict branch on purpose. A value that sits
    # outside *every* published source is implausible whether or not those
    # sources agree with each other, and "the literature disagrees" is the
    # less useful thing to tell someone holding a 50 fM result. Ordering
    # conflict first made it swallow every other verdict on real data,
    # where most targets have some genuine disagreement.
    envelope_low = prior.envelope_low_m
    if envelope_low is not None and kd < envelope_low / TIGHTER_THAN_EXPECTED_MULTIPLE:
        return Verdict(
            label=VerdictLabel.OUTSIDE_KNOWN_RANGE_FLAG,
            flag_for_review=True,
            confidence=prior.calibrated_confidence,
            rationale=(
                f"{result.sequence_name}: measured KD ({_fmt_m(kd)}) is more than "
                f"{TIGHTER_THAN_EXPECTED_MULTIPLE:.0f}x tighter than the tightest affinity "
                f"reported for {result.target_name} in any source reviewed "
                f"({_fmt_m(envelope_low)}); the bulk of reported values sit at "
                f"{_fmt_m(prior.low_m)}-{_fmt_m(prior.high_m)}. Implausible against the "
                "literature as a whole - recommend orthogonal confirmation (e.g. monovalent "
                "SPR) before this reaches a customer report."
            ),
            citations=citations,
        )

    if prior.density == LiteratureDensity.SPARSE:
        return Verdict(
            label=VerdictLabel.NOVEL_CANDIDATE,
            flag_for_review=True,
            confidence=prior.calibrated_confidence,
            rationale=(
                f"{result.sequence_name}: binding detected against {result.target_name} "
                f"(KD = {_fmt_m(kd)}), but the literature-grounding layer found only "
                f"{prior.n_abstracts} relevant abstract(s) for this target, with no established "
                "binder/epitope precedent (calibrated confidence "
                f"{prior.calibrated_confidence:.2f} - below the reliable-prior threshold). "
                "This isn't inconsistent with anything known - there's just very little to compare "
                "against. Recommend treating as a potentially novel binder: prioritize for epitope "
                "mapping and an orthogonal confirmation assay rather than an immediate customer report."
            ),
            citations=citations,
        )

    if prior.low_m is None or prior.high_m is None:
        return Verdict(
            label=VerdictLabel.QUALITATIVE_LITERATURE_ONLY,
            flag_for_review=False,
            confidence=prior.calibrated_confidence,
            rationale=(
                f"{result.sequence_name}: binding detected against {result.target_name} "
                f"(KD = {_fmt_m(kd)}). The literature layer found known binders/epitopes "
                f"({', '.join(prior.known_binders) or 'none named'}) but no extractable numeric "
                "affinity range to compare against, so no quantitative flag applies here."
            ),
            citations=citations,
        )

    if prior.caveat_ceiling_m is not None and kd < prior.caveat_ceiling_m:
        return Verdict(
            label=VerdictLabel.OUTSIDE_KNOWN_RANGE_FLAG,
            flag_for_review=True,
            confidence=prior.calibrated_confidence,
            rationale=(
                f"{result.sequence_name}: measured KD ({_fmt_m(kd)}) is tighter than the literature "
                f"explicitly flags as uncommon for {result.target_name} (affinities below "
                f"{_fmt_m(prior.caveat_ceiling_m)} are noted in the literature as usually reflecting "
                "avidity/multivalency effects rather than true monovalent 1:1 affinity, given known "
                f"validated binders cluster at {_fmt_m(prior.low_m)}-{_fmt_m(prior.high_m)}). "
                "Recommend orthogonal confirmation (e.g. monovalent SPR) before this reaches a "
                "customer report."
            ),
            citations=citations,
        )

    if kd < prior.low_m / TIGHTER_THAN_EXPECTED_MULTIPLE:
        if prior.evidence_level == EvidenceLevel.CONFLICTING:
            return _conflict_verdict(result, kd, prior)
        return Verdict(
            label=VerdictLabel.OUTSIDE_KNOWN_RANGE_FLAG,
            flag_for_review=True,
            confidence=prior.calibrated_confidence,
            rationale=(
                f"{result.sequence_name}: measured KD ({_fmt_m(kd)}) is more than "
                f"{TIGHTER_THAN_EXPECTED_MULTIPLE:.0f}x tighter than any literature precedent for "
                f"{result.target_name} (known validated binders: {_fmt_m(prior.low_m)}-"
                f"{_fmt_m(prior.high_m)}). Statistically implausible relative to the literature - "
                "recommend orthogonal confirmation before reporting."
            ),
            citations=citations,
        )

    if kd > prior.high_m * WEAKER_THAN_EXPECTED_MULTIPLE:
        if prior.evidence_level == EvidenceLevel.CONFLICTING:
            return _conflict_verdict(result, kd, prior)
        return Verdict(
            label=VerdictLabel.WEAKER_THAN_TYPICAL,
            flag_for_review=False,
            confidence=prior.calibrated_confidence,
            rationale=(
                f"{result.sequence_name}: binding detected but measured KD ({_fmt_m(kd)}) is "
                f"substantially weaker than established binders for {result.target_name} "
                f"({_fmt_m(prior.low_m)}-{_fmt_m(prior.high_m)}). Not an artifact concern, just "
                "lower priority for follow-up relative to tighter candidates in the same batch."
            ),
            citations=citations,
        )

    # Two ways to reach this verdict, and they are not the same claim. Inside
    # the core is "the literature reports values like this one". Outside the
    # core but inside the tolerance band is "close enough that flagging it
    # would be noise" - which is a weaker statement and has to read as one.
    # Saying "falls within the range (3.16e-13 M-1.34e-09 M)" for a 1.70e-09 M
    # result is self-contradicting on its face, and the first thing a careful
    # reader checks is whether the number is actually inside the interval
    # printed next to it.
    inside_core = prior.low_m <= kd <= prior.high_m
    if inside_core:
        placement = (
            f"falls within the range reported for validated binders "
            f"({_fmt_m(prior.low_m)}-{_fmt_m(prior.high_m)}"
        )
    else:
        tighter = kd < prior.low_m
        factor = prior.low_m / kd if tighter else kd / prior.high_m
        placement = (
            f"sits just outside the range reported for validated binders - "
            f"{factor:.1f}x {'tighter than its lower' if tighter else 'weaker than its upper'} "
            f"bound, well inside the {WEAKER_THAN_EXPECTED_MULTIPLE:.0f}x band that absorbs "
            f"ordinary between-paper variation ({_fmt_m(prior.low_m)}-{_fmt_m(prior.high_m)}"
        )

    return Verdict(
        label=VerdictLabel.CONSISTENT_WITH_LITERATURE,
        flag_for_review=False,
        confidence=prior.calibrated_confidence,
        rationale=(
            f"{result.sequence_name}: measured KD ({_fmt_m(kd)}) against {result.target_name} "
            f"{placement}, known binders: {', '.join(prior.known_binders) or 'n/a'}). "
            f"Expected result, calibrated confidence {prior.calibrated_confidence:.2f}. No flag."
        ),
        citations=citations,
    )
