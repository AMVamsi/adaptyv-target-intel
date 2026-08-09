"""
Named-entity extraction over target-literature abstracts.

Design note (read this before judging the extraction quality): the MSc
thesis this project reuses the architecture of fine-tunes PubMedBERT for
NER (0.884 test F1) over a labeled IBD-literature corpus. That fine-tuning
requires GPU training time and thousands of labeled examples this
take-home doesn't have. Rather than fake a "fine-tuned model" that isn't
one, this module is an honest, fast, fully offline **rule/gazetteer-based
extractor** that plays the same architectural role: given raw abstract
text, emit typed entity spans that the relation-extraction tier consumes.

Swapping this for a real fine-tuned PubMedBERT NER model (as in the
thesis) would be a drop-in replacement - `extract_entities()` is the only
function the rest of the pipeline depends on, and its return type would
not need to change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum


class EntityType(str, Enum):
    BINDER_NAMED = "binder_named"          # e.g. "trastuzumab"
    BINDER_GENERIC = "binder_generic"      # e.g. "nanobody", "scFv"
    EPITOPE = "epitope"                    # e.g. "domain IV"
    AFFINITY_VALUE = "affinity_value"      # a parsed (low_M, high_M) range
    SPARSITY_SIGNAL = "sparsity_signal"    # phrases indicating thin literature


@dataclass(frozen=True)
class Entity:
    type: EntityType
    text: str
    start: int
    end: int
    # populated only for AFFINITY_VALUE entities: (low_molar, high_molar)
    value_range_m: tuple[float, float] | None = None
    # AFFINITY_VALUE only: does the surrounding text actually present this
    # number as a binding affinity? See is_affinity_qualified.
    qualified: bool = True


# A small gazetteer of named binders. In a production system this would be
# a curated dictionary (e.g. derived from DrugBank/INN lists) or the output
# of a trained NER model; here it's illustrative and intentionally short.
_NAMED_BINDERS = [
    "trastuzumab", "pertuzumab", "cetuximab", "necitumumab", "panitumumab",
    "atezolizumab", "durvalumab", "adalimumab", "infliximab", "golimumab",
    "ustekinumab", "briakinumab", "omalizumab",
]

_GENERIC_BINDER_TERMS = [
    "nanobody", "nanobodies", "vhh", "scfv", "single-domain binder",
    "single-domain binders", "engineered small binder", "antibody", "antibodies",
    "binder-scaffold", "binder scaffolds",
]

_EPITOPE_PATTERN = re.compile(
    r"domain\s+(I|II|III|IV|V)\b|IgV domain",
    re.IGNORECASE,
)

_SPARSITY_PHRASES = [
    "very few", "to date there are very few", "no widely cited validated",
    "uncommon in the published literature", "orphan",
]

_UNIT_TO_MOLAR = {
    # Spelled-out forms. Case-insensitive: no ambiguity to preserve.
    "femtomolar": 1e-15, "picomolar": 1e-12, "nanomolar": 1e-9, "micromolar": 1e-6,
    # Symbol forms, as they actually appear in real abstracts. These are
    # matched CASE-SENSITIVELY and the capital M is mandatory, because
    # "nm" is nanometres and "nM" is nanomolar - a case-insensitive match
    # reads particle diameters out of a nanotech paper and reports them as
    # binding affinities. Verified against live PubMed output, where the
    # first apparent "affinity" hit on an ERBB2 query was exactly this
    # false positive.
    "fM": 1e-15, "pM": 1e-12, "nM": 1e-9, "µM": 1e-6, "μM": 1e-6, "uM": 1e-6,
}

# fM..µM only. Bare "M" and "mM" are deliberately excluded: at those
# concentrations a number is almost always a buffer or reagent ("2 M NaCl",
# "10 mM Tris"), not a binding affinity, and admitting them buys a little
# recall for a lot of garbage.
_SYMBOL_UNIT = r"fM|pM|nM|µM|μM|uM"
_WORD_UNIT = r"femtomolar|picomolar|nanomolar|micromolar"

# Ranges: "0.1 to 5 nanomolar", "0.1-5 nM", "0.1 – 5 nM".
_RANGE_PATTERN = re.compile(
    rf"(\d+(?:\.\d+)?)\s*(?:to|[-–—])\s*(\d+(?:\.\d+)?)\s*({_WORD_UNIT})",
    re.IGNORECASE,
)
_RANGE_SYMBOL_PATTERN = re.compile(
    rf"(\d+(?:\.\d+)?)\s*(?:to|[-–—])\s*(\d+(?:\.\d+)?)\s*({_SYMBOL_UNIT})(?![A-Za-z])"
)
_SINGLE_PATTERN = re.compile(rf"(\d+(?:\.\d+)?)\s*({_WORD_UNIT})", re.IGNORECASE)
_SINGLE_SYMBOL_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*({_SYMBOL_UNIT})(?![A-Za-z])"
)
_QUALITATIVE_PATTERN = re.compile(
    r"(sub-nanomolar|low nanomolar|high nanomolar|sub-picomolar)", re.IGNORECASE
)
_BELOW_PATTERN = re.compile(
    rf"below\s+(\d+(?:\.\d+)?)\s*({_WORD_UNIT})", re.IGNORECASE
)
_BELOW_SYMBOL_PATTERN = re.compile(
    rf"below\s+(\d+(?:\.\d+)?)\s*({_SYMBOL_UNIT})(?![A-Za-z])"
)


def _multiplier(unit: str) -> float:
    """Word units are case-insensitive; symbol units are not (nM vs nm)."""
    if unit in _UNIT_TO_MOLAR:
        return _UNIT_TO_MOLAR[unit]
    return _UNIT_TO_MOLAR[unit.lower()]

_QUALITATIVE_RANGES_M = {
    "sub-nanomolar": (1e-10, 1e-9),
    "low nanomolar": (1e-9, 1e-8),
    "high nanomolar": (1e-8, 1e-7),
    "sub-picomolar": (1e-13, 1e-12),
}


# A number with molar units is not automatically a binding affinity. Real
# abstracts are full of them: inhibitory potencies, working concentrations,
# detection limits, doses. Admitting all of them is what made HER2's
# "expected affinity range" span 1e-13 to 1.5e-7 M on real PubMed text -
# five orders of magnitude, which is not a prior, it's noise.
#
# So an affinity value counts only when the surrounding text presents it as
# a dissociation constant, and is rejected outright when a nearby cue says
# it's a different quantity. This is the job the fine-tuned relation
# classifier does in the thesis pipeline; here it is a proximity rule, and
# the README reports what it does and doesn't recover.
_AFFINITY_CUES = re.compile(
    r"\bK\s*[Dd]\b|\bK[_-]?D\b|\bKd\b|dissociation constant|binding affinit|"
    r"\baffinit(?:y|ies)\b|\bbinds?\s+(?:to\s+\S+\s+)?with\b|\bbound\s+with\b|\bavidity\b",
    re.IGNORECASE,
)
_DISQUALIFY_CUES = re.compile(
    r"\bIC\s*50\b|\bEC\s*50\b|\bK\s*i\b|\bLD\s*50\b|concentrations?\s+of|"
    r"\bdose[sd]?\b|\bincubated\b|\bbuffer\b|\bdetection limit\b|\btreated with\b",
    re.IGNORECASE,
)

# How far either side of the number to look. Roughly a clause: wide enough
# to catch "the nanobody bound the receptor with a KD of 2.3 nM", narrow
# enough that a KD mentioned two sentences away doesn't license an
# unrelated concentration.
_CUE_WINDOW = 120
_DISQUALIFY_WINDOW = 50


def is_affinity_qualified(text: str, entity: Entity) -> bool:
    """Does the context present this number as a binding affinity?

    Qualification requires an affinity cue nearby, and rejects the value if
    a competing-quantity cue sits closer than the window allows - "IC50 of
    120 nM" should never widen a KD prior.
    """
    # "KD = 5.71 +/- 3.89 nM" carries one measurement, not two. The number
    # after the +/- is the uncertainty, and it sits right next to a genuine
    # affinity cue, so every contextual test passes it. Observed in real
    # output (PMID 40849046), where the error term was being recorded as an
    # independent reported affinity and pulled the distribution with it.
    preceding = text[max(0, entity.start - 4):entity.start]
    if "±" in preceding or "+/-" in preceding:
        return False

    lo = max(0, entity.start - _CUE_WINDOW)
    hi = min(len(text), entity.end + _CUE_WINDOW)
    window = text[lo:hi]

    if not _AFFINITY_CUES.search(window):
        return False

    tight = text[max(0, entity.start - _DISQUALIFY_WINDOW):min(len(text), entity.end + _DISQUALIFY_WINDOW)]
    return not _DISQUALIFY_CUES.search(tight)


def _longest_non_overlapping(candidates: list[Entity]) -> list[Entity]:
    """Resolve overlapping gazetteer matches, longest span wins.

    Ties break on earlier start, so the result is deterministic regardless
    of the order terms happen to sit in the gazetteer lists.
    """
    chosen: list[Entity] = []
    for cand in sorted(candidates, key=lambda e: (-(e.end - e.start), e.start)):
        if any(cand.start < kept.end and kept.start < cand.end for kept in chosen):
            continue
        chosen.append(cand)
    return sorted(chosen, key=lambda e: e.start)


def extract_entities(text: str) -> list[Entity]:
    entities: list[Entity] = []

    # Real PubMed text separates a value from its unit with a non-breaking
    # space ("0.8\xa0nM"). Normalising to a plain space keeps every offset
    # below aligned with the original string (1 char -> 1 char) while
    # letting the patterns match.
    text = text.replace("\xa0", " ").replace(" ", " ").replace(" ", " ")
    low = text.lower()

    # Gazetteer terms overlap by construction ("single-domain binder" is a
    # prefix of "single-domain binders"; "engineered small binder" and
    # "binder scaffolds" both fire inside "engineered small binder
    # scaffolds"). Emitting every match counts one mention two or three
    # times, which inflates the tier-2 hit count and duplicates entries in
    # `known_binders`. Collect candidates first, then keep the longest span
    # per overlapping region.
    binder_candidates: list[Entity] = []
    for name in _NAMED_BINDERS:
        for m in re.finditer(re.escape(name), low):
            binder_candidates.append(
                Entity(EntityType.BINDER_NAMED, text[m.start():m.end()], m.start(), m.end())
            )
    for term in _GENERIC_BINDER_TERMS:
        for m in re.finditer(re.escape(term), low):
            binder_candidates.append(
                Entity(EntityType.BINDER_GENERIC, text[m.start():m.end()], m.start(), m.end())
            )
    entities.extend(_longest_non_overlapping(binder_candidates))

    for m in _EPITOPE_PATTERN.finditer(text):
        entities.append(Entity(EntityType.EPITOPE, m.group(0), m.start(), m.end()))

    # Order matters: the widest, most specific patterns claim their spans
    # first, and each later pass skips anything already covered. Otherwise
    # "100 pM" inside "below 100 pM" registers twice - once as a caveat
    # ceiling and once as an ordinary observed value, which would drag the
    # expected-affinity range down to include a number the literature was
    # explicitly warning about.
    for pattern in (_RANGE_PATTERN, _RANGE_SYMBOL_PATTERN):
        for m in pattern.finditer(text):
            low_v, high_v, unit = m.groups()
            mult = _multiplier(unit)
            entities.append(Entity(
                EntityType.AFFINITY_VALUE, m.group(0), m.start(), m.end(),
                value_range_m=(float(low_v) * mult, float(high_v) * mult),
            ))

    for m in _QUALITATIVE_PATTERN.finditer(text):
        phrase = m.group(0).lower()
        entities.append(Entity(
            EntityType.AFFINITY_VALUE, m.group(0), m.start(), m.end(),
            value_range_m=_QUALITATIVE_RANGES_M[phrase],
        ))

    # "below X" is a ceiling the literature flags as uncommon, not a typical
    # observed range - tagged distinctly so relation_extraction can treat it
    # as a caution signal rather than evidence of a tighter binder.
    for pattern in (_BELOW_PATTERN, _BELOW_SYMBOL_PATTERN):
        for m in pattern.finditer(text):
            value, unit = m.groups()
            v = float(value) * _multiplier(unit)
            entities.append(Entity(
                EntityType.AFFINITY_VALUE, "caveat:" + m.group(0), m.start(), m.end(),
                value_range_m=(0.0, v),
            ))

    def _already_covered(start: int, end: int) -> bool:
        return any(
            e.start <= start and end <= e.end
            for e in entities if e.type == EntityType.AFFINITY_VALUE
        )

    for pattern in (_SINGLE_PATTERN, _SINGLE_SYMBOL_PATTERN):
        for m in pattern.finditer(text):
            if _already_covered(m.start(), m.end()):
                continue
            value, unit = m.groups()
            v = float(value) * _multiplier(unit)
            entities.append(Entity(
                EntityType.AFFINITY_VALUE, m.group(0), m.start(), m.end(),
                value_range_m=(v, v),
            ))

    for phrase in _SPARSITY_PHRASES:
        for m in re.finditer(re.escape(phrase), low):
            entities.append(Entity(EntityType.SPARSITY_SIGNAL, text[m.start():m.end()], m.start(), m.end()))

    # Stamp each affinity with whether its context actually presents it as a
    # binding constant. The span is still returned either way - the scorer
    # measures span detection, and dropping unqualified spans here would
    # conflate "found the number" with "believed the number".
    return [
        (
            e
            if e.type is not EntityType.AFFINITY_VALUE
            else replace(e, qualified=e.text.startswith("caveat:") or is_affinity_qualified(text, e))
        )
        for e in entities
    ]
