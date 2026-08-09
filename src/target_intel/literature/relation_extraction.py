"""
Relation extraction: turn per-abstract entities into a per-target
"literature claim" about the known affinity/epitope landscape.

Architecture mirrors the thesis's three-tier ensemble (supervised /
heuristic / zero-shot NLI), with the same idea - combine a precise-but-
narrow signal, a loose-but-recall-heavy signal, and a semantic-plausibility
signal - reimplemented without a supervised model or a downloadable NLI
checkpoint (this sandbox has no access to model-hosting domains), so each
tier here is a deliberately simple, fully-offline stand-in:

  Tier 1 - pattern-based:      binder + epitope + affinity-value entities
                                co-occurring in the same abstract. High
                                precision, narrow recall.
  Tier 2 - proximity heuristic: any binder mention + any affinity value in
                                the same abstract, regardless of whether an
                                epitope was named. Wider recall, weaker
                                precision - generic binder mentions score
                                lower than named ones.
  Tier 3 - lexical NLI-lite:   scores whether the abstract text lexically
                                "entails" the hypothesis "this target has an
                                established, validated binder epitope" vs.
                                "this target's literature is sparse", via
                                keyword-overlap scoring rather than a real
                                entailment model.

The three tiers vote into one raw ensemble score per target, which
`calibration.py` then calibrates against the small golden set.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from .corpus import DemoAbstract
from .ner import Entity, EntityType, extract_entities

_ESTABLISHED_HYPOTHESIS_KEYWORDS = [
    "validated", "clinically validated", "well-characterized", "dominant",
    "widely cited",
]
_SPARSE_HYPOTHESIS_KEYWORDS = [
    "very few", "sparse", "orphan", "uncommon", "no widely cited",
]


class EvidenceLevel(str, Enum):
    """A second, orthogonal axis to raw_confidence/density. Density answers
    "how much literature is there"; EvidenceLevel answers "do the sources
    that exist actually agree with each other". A target can have RICH
    density (lots of abstracts found) and still be CONFLICTING (those
    abstracts disagree on the number) - that combination is exactly the
    case a single confidence score would hide and this field exists to
    surface (see CD20 in the demo corpus)."""

    VERIFIED = "verified"        # >=2 independent quantitative sources, ranges agree
    CLAIMED = "claimed"          # exactly 1 quantitative source - plausible, unconfirmed
    CONFLICTING = "conflicting"  # >=2 quantitative sources, ranges don't overlap
    MISSING = "missing"          # no quantitative source at all


@dataclass
class SourceClaim:
    """One abstract's own, independent affinity claim - kept separate from
    the aggregate so cross-source agreement can be checked before ranges
    get silently merged into one blended min/max."""

    pmid: str
    affinity_range_m: tuple[float, float] | None
    has_hit: bool  # at least one binder AND one affinity value co-occur


@dataclass
class Provenance:
    pmid: str
    tier: str
    snippet: str


@dataclass
class TargetLiteratureClaim:
    target_hint: str
    known_binders: set[str] = field(default_factory=set)
    known_epitopes: set[str] = field(default_factory=set)
    affinity_low_m: float | None = None
    affinity_high_m: float | None = None
    caveat_ceiling_m: float | None = None  # "affinities below X are uncommon" signal
    n_abstracts: int = 0
    n_tier1_hits: int = 0
    n_tier2_hits: int = 0
    sparsity_votes: int = 0
    established_votes: int = 0
    raw_confidence: float = 0.0
    # Every qualified affinity value seen, one entry per extracted span.
    # Kept alongside the min/max envelope because on real literature the
    # envelope is nearly useless as a prior: HER2's spans 1e-13 to 1.5e-7 M,
    # five orders of magnitude, since published binders run from engineered
    # sub-picomolar constructs to weak clones in affinity-maturation papers.
    # That range is real, not an extraction error - it just can't answer
    # "is this result typical". The distribution can.
    affinity_values_m: list[float] = field(default_factory=list)
    provenance: list[Provenance] = field(default_factory=list)
    evidence_level: EvidenceLevel = EvidenceLevel.MISSING
    # populated only when evidence_level == CONFLICTING: (pmid, low_m, high_m)
    # for every source whose range didn't agree with at least one other.
    conflicting_source_ranges: list[tuple[str, float, float]] = field(default_factory=list)


def typical_range_m(values: list[float]) -> tuple[float | None, float | None]:
    """A robust core range from the distribution of reported affinities.

    The min/max envelope is not a usable prior on real literature: HER2's
    spans five orders of magnitude, because published binders run from
    engineered sub-picomolar constructs to weak clones in an
    affinity-maturation paper. Both extremes are real, so the envelope
    can't be narrowed by better extraction - it has to be summarised
    differently.

    Affinities are log-normal, so quartiles are taken in log10 space. The
    interquartile range answers "where do reported values actually sit",
    which is the question a plausibility check needs, and one outlier paper
    can no longer stretch the prior.

    Below 4 values the quartiles are meaningless, so the envelope is
    returned unchanged rather than a falsely precise core.
    """
    usable = sorted(v for v in values if v and v > 0)
    if len(usable) < 4:
        return (usable[0], usable[-1]) if usable else (None, None)

    logs = [math.log10(v) for v in usable]

    def _quantile(q: float) -> float:
        pos = q * (len(logs) - 1)
        lo_i = int(math.floor(pos))
        hi_i = min(lo_i + 1, len(logs) - 1)
        frac = pos - lo_i
        return logs[lo_i] * (1 - frac) + logs[hi_i] * frac

    return 10 ** _quantile(0.25), 10 ** _quantile(0.75)


def _tier1_pattern(entities: list[Entity], pmid: str, text: str, claim: TargetLiteratureClaim) -> bool:
    binders = [e for e in entities if e.type in (EntityType.BINDER_NAMED, EntityType.BINDER_GENERIC)]
    epitopes = [e for e in entities if e.type == EntityType.EPITOPE]
    affinities = [
        e for e in entities
        if e.type == EntityType.AFFINITY_VALUE
        and not e.text.startswith("caveat:")
        and e.qualified  # context must present the number as a binding constant
    ]

    if binders and epitopes and affinities:
        for b in binders:
            claim.known_binders.add(b.text.lower())
        for ep in epitopes:
            claim.known_epitopes.add(ep.text)
        for aff in affinities:
            lo, hi = aff.value_range_m
            claim.affinity_low_m = lo if claim.affinity_low_m is None else min(claim.affinity_low_m, lo)
            claim.affinity_high_m = hi if claim.affinity_high_m is None else max(claim.affinity_high_m, hi)
            claim.affinity_values_m.extend([lo, hi] if lo != hi else [lo])
        claim.provenance.append(Provenance(pmid, "tier1_pattern", text[:140]))
        return True
    return False


def _tier2_proximity(entities: list[Entity], pmid: str, text: str, claim: TargetLiteratureClaim) -> bool:
    binders = [e for e in entities if e.type in (EntityType.BINDER_NAMED, EntityType.BINDER_GENERIC)]
    affinities = [
        e for e in entities
        if e.type == EntityType.AFFINITY_VALUE
        and not e.text.startswith("caveat:")
        and e.qualified  # context must present the number as a binding constant
    ]
    if binders and affinities:
        for b in binders:
            claim.known_binders.add(b.text.lower())
        for aff in affinities:
            lo, hi = aff.value_range_m
            claim.affinity_low_m = lo if claim.affinity_low_m is None else min(claim.affinity_low_m, lo)
            claim.affinity_high_m = hi if claim.affinity_high_m is None else max(claim.affinity_high_m, hi)
            claim.affinity_values_m.extend([lo, hi] if lo != hi else [lo])
        claim.provenance.append(Provenance(pmid, "tier2_proximity", text[:140]))
        return True
    return False


def _tier3_lexical_nli(text: str, claim: TargetLiteratureClaim) -> None:
    low = text.lower()
    established_score = sum(1 for kw in _ESTABLISHED_HYPOTHESIS_KEYWORDS if kw in low)
    sparse_score = sum(1 for kw in _SPARSE_HYPOTHESIS_KEYWORDS if kw in low)
    # Sparsity phrases win ties: a sentence like "no widely cited validated
    # epitope" contains "validated" as a bare substring despite negating it -
    # bag-of-words scoring can't see the negation, so the rarer, more
    # specific sparsity signal is treated as the more reliable one whenever
    # both fire on the same abstract.
    if sparse_score > 0:
        claim.sparsity_votes += 1
    elif established_score > 0:
        claim.established_votes += 1


def _apply_caveats(entities: list[Entity], claim: TargetLiteratureClaim) -> None:
    for e in entities:
        if e.type == EntityType.AFFINITY_VALUE and e.text.startswith("caveat:"):
            _, ceiling = e.value_range_m
            claim.caveat_ceiling_m = ceiling if claim.caveat_ceiling_m is None else min(claim.caveat_ceiling_m, ceiling)


def _entities_for(abstract) -> list[Entity]:
    """Spans for one abstract.

    Snapshot records carry spans extracted once, at build time, from the
    complete abstract; the stored `text` is only a short provenance excerpt.
    Re-extracting from that excerpt would silently score a truncated input
    and produce a weaker claim than the pipeline actually derived. Hand-
    written demo abstracts carry no spans, so they are extracted live.
    """
    pre_extracted = getattr(abstract, "entities", None)
    return list(pre_extracted) if pre_extracted else extract_entities(abstract.text)


def _extract_source_claim(abstract) -> SourceClaim:
    """A single abstract's own affinity claim, kept independent of every
    other abstract - the input to conflict detection. Deliberately
    re-derives entities rather than reusing tier1/tier2's side effects, so
    this analysis is purely additive and can't perturb the existing
    aggregate logic above."""
    pmid = abstract.pmid_placeholder
    entities = _entities_for(abstract)
    binders = [e for e in entities if e.type in (EntityType.BINDER_NAMED, EntityType.BINDER_GENERIC)]
    affinities = [
        e for e in entities
        if e.type == EntityType.AFFINITY_VALUE
        and not e.text.startswith("caveat:")
        and e.qualified  # context must present the number as a binding constant
    ]

    if not (binders and affinities):
        return SourceClaim(pmid=pmid, affinity_range_m=None, has_hit=False)

    lo = min(a.value_range_m[0] for a in affinities)
    hi = max(a.value_range_m[1] for a in affinities)
    return SourceClaim(pmid=pmid, affinity_range_m=(lo, hi), has_hit=True)


def _ranges_overlap(r1: tuple[float, float], r2: tuple[float, float], tolerance: float = 3.0) -> bool:
    """Widens each range by `tolerance`x on both sides before comparing.
    Without this, two papers independently reporting "2nM" and "3nM" for
    the same epitope - ordinary, expected variation between assays - would
    register as a hard conflict, since as exact point values they don't
    literally overlap. A tolerance band absorbs that kind of routine
    variation while still catching a genuine format-driven discrepancy
    like CD20's ~100x gap between bivalent-IgG and monovalent-nanobody
    reports, which is far larger than ordinary measurement noise."""
    lo1, hi1 = r1[0] / tolerance, r1[1] * tolerance
    lo2, hi2 = r2[0] / tolerance, r2[1] * tolerance
    return lo1 <= hi2 and lo2 <= hi1


# A source counts as agreeing with the consensus if its range overlaps the
# robust core. Below this fraction there is no consensus to speak of.
_CONSENSUS_FRACTION = 2 / 3
# Below this many quantitative sources, use the pairwise disagreement test.
_MIN_SOURCES_FOR_CONSENSUS = 4


def _determine_evidence_level(
    source_claims: list[SourceClaim], core: tuple[float | None, float | None]
) -> tuple[EvidenceLevel, list[tuple[str, float, float]]]:
    """Do the sources agree, checked against the consensus rather than
    pairwise.

    The original rule flagged CONFLICTING if *any* pair of sources failed to
    overlap. That is right for a handful of tidy sources and useless on real
    literature: across 4 papers reporting anything from sub-picomolar
    engineered constructs to 151 nM affinity-maturation starting points,
    some pair always disagrees, so every real target came back CONFLICTING
    and the signal carried no information.

    What a scientist actually wants to know is whether the reported values
    cluster. So each source is compared against the robust core (the log-
    space IQR): if at least two thirds of sources overlap it, that is a
    consensus with outliers - VERIFIED, with the outliers still listed.
    Below that, the literature genuinely does not agree - CONFLICTING.
    """
    with_hits = [s for s in source_claims if s.has_hit and s.affinity_range_m is not None]

    if not with_hits:
        return EvidenceLevel.MISSING, []
    if len(with_hits) == 1:
        return EvidenceLevel.CLAIMED, []

    core_low, core_high = core

    # With only a handful of sources the consensus test is degenerate: the
    # core is computed from the very values being tested against it, so
    # every source trivially overlaps it and nothing is ever flagged. Two
    # sources reporting ranges 100x apart is exactly the case worth
    # surfacing, and only the pairwise test catches it. Above the threshold
    # the pairwise test becomes the useless one - across enough real papers
    # some pair always disagrees - so the consensus test takes over.
    if len(with_hits) < _MIN_SOURCES_FOR_CONSENSUS or core_low is None or core_high is None:
        conflicting_pmids: set[str] = set()
        for i in range(len(with_hits)):
            for j in range(i + 1, len(with_hits)):
                if not _ranges_overlap(with_hits[i].affinity_range_m, with_hits[j].affinity_range_m):
                    conflicting_pmids.update({with_hits[i].pmid, with_hits[j].pmid})
        if conflicting_pmids:
            return EvidenceLevel.CONFLICTING, [
                (s.pmid, s.affinity_range_m[0], s.affinity_range_m[1])
                for s in with_hits if s.pmid in conflicting_pmids
            ]
        return EvidenceLevel.VERIFIED, []

    agreeing: list[SourceClaim] = []
    outliers: list[SourceClaim] = []
    for source in with_hits:
        (agreeing if _ranges_overlap(source.affinity_range_m, (core_low, core_high)) else outliers).append(source)

    dissenting = [(s.pmid, s.affinity_range_m[0], s.affinity_range_m[1]) for s in outliers]

    if len(agreeing) / len(with_hits) >= _CONSENSUS_FRACTION:
        return EvidenceLevel.VERIFIED, dissenting
    return EvidenceLevel.CONFLICTING, dissenting


def build_target_claim(target_hint: str, abstracts: list[DemoAbstract] | list) -> TargetLiteratureClaim:
    """Run the 3-tier ensemble over all abstracts for one target and return
    a single aggregated, provenance-tagged claim."""
    claim = TargetLiteratureClaim(target_hint=target_hint)
    relevant = [a for a in abstracts if getattr(a, "target_hint", None) == target_hint]
    claim.n_abstracts = len(relevant)

    for abstract in relevant:
        entities = _entities_for(abstract)
        hit1 = _tier1_pattern(entities, abstract.pmid_placeholder, abstract.text, claim)
        if hit1:
            claim.n_tier1_hits += 1
        else:
            hit2 = _tier2_proximity(entities, abstract.pmid_placeholder, abstract.text, claim)
            if hit2:
                claim.n_tier2_hits += 1
        _tier3_lexical_nli(abstract.text, claim)
        _apply_caveats(entities, claim)

    claim.raw_confidence = _ensemble_score(claim)

    source_claims = [_extract_source_claim(a) for a in relevant]
    claim.evidence_level, claim.conflicting_source_ranges = _determine_evidence_level(
        source_claims, typical_range_m(claim.affinity_values_m)
    )

    return claim


def _ensemble_score(claim: TargetLiteratureClaim) -> float:
    """Weighted vote across the three tiers into a single raw [0,1] score.
    Weights: tier1 hits count most (precise structured evidence), tier2
    less, tier3's established/sparse vote nudges the score up or down.
    This raw score is *not* the final confidence shown to a scientist -
    `calibration.py` recalibrates it against the golden set first."""
    if claim.n_abstracts == 0:
        return 0.0
    tier1_component = 0.5 * min(claim.n_tier1_hits / max(claim.n_abstracts, 1), 1.0)
    tier2_component = 0.25 * min(claim.n_tier2_hits / max(claim.n_abstracts, 1), 1.0)
    vote_total = claim.established_votes + claim.sparsity_votes
    tier3_component = 0.25 * (claim.established_votes / vote_total) if vote_total else 0.0
    return round(tier1_component + tier2_component + tier3_component, 4)
