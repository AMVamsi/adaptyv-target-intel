"""
Exact-span entity scoring.

Ported from the entity-level F1 scorer in the MSc thesis pipeline
(`src/module3_ner/stage5_evaluation/f1_scorer.py`), where it is the primary
metric for comparing five NER tiers against one gold standard. The matching
rule is unchanged: a prediction counts as a true positive only on an exact
match of `(sentence_id, char_start, char_end, entity_type)`. Partial overlap
is a miss, and a right span with the wrong type is both a false positive and
a false negative.

Why this matters here: without it, "the NER is a documented rule-based
stand-in" is an unfalsifiable claim. With it, the extractor has a number,
that number is reproducible, and a future swap to a trained model can be
justified by a measured delta instead of an assumption.

`micro` pools TP/FP/FN across all types and computes one corpus-level P/R/F1.
It is deliberately not a type-averaged macro: with a handful of AFFINITY
spans and many BINDER spans, a macro average would let a rare type swing the
headline figure.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .ner import Entity, EntityType

# (char_start, char_end, entity_type) within one sentence.
_SpanKey = tuple[int, int, str]


@dataclass
class TypeScore:
    precision: float
    recall: float
    f1: float
    support: int  # gold spans of this type
    tp: int
    fp: int
    fn: int


@dataclass
class ScoreResult:
    """Corpus-level micro scores plus a per-type breakdown."""

    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    support: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    per_type: dict[str, TypeScore] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "micro": {
                "precision": round(self.precision, 4),
                "recall": round(self.recall, 4),
                "f1": round(self.f1, 4),
                "support": self.support,
                "tp": self.tp,
                "fp": self.fp,
                "fn": self.fn,
            },
            "per_type": {
                name: {
                    "precision": round(s.precision, 4),
                    "recall": round(s.recall, 4),
                    "f1": round(s.f1, 4),
                    "support": s.support,
                    "tp": s.tp,
                    "fp": s.fp,
                    "fn": s.fn,
                }
                for name, s in sorted(self.per_type.items())
            },
        }


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def score_spans(
    predictions: dict[str, list[Entity]],
    gold: dict[str, list[Entity]],
    types: tuple[EntityType, ...] | None = None,
) -> ScoreResult:
    """Score predicted entities against gold, keyed by sentence id.

    `types` restricts scoring to a subset. This is not a convenience: some
    entity types in this extractor have no gold annotations at all
    (SPARSITY_SIGNAL is a soft textual cue, not a span anyone would label
    consistently), and silently scoring an unannotated type against an empty
    gold set would report it as 0.0 precision and drag the micro average
    down for a reason that has nothing to do with model quality. The thesis
    hits the identical problem with GENE/PHENOTYPE and resolves it the same
    way - report those types as *not evaluated* rather than as zero.
    """
    keep = {t.value for t in types} if types else None

    def _index(entities: dict[str, list[Entity]]) -> dict[str, set[_SpanKey]]:
        out: dict[str, set[_SpanKey]] = defaultdict(set)
        for sid, spans in entities.items():
            for e in spans:
                if keep is None or e.type.value in keep:
                    out[sid].add((e.start, e.end, e.type.value))
        return out

    gold_idx = _index(gold)
    pred_idx = _index(predictions)

    tp_t: dict[str, int] = defaultdict(int)
    fp_t: dict[str, int] = defaultdict(int)
    fn_t: dict[str, int] = defaultdict(int)

    for sid in set(gold_idx) | set(pred_idx):
        g = gold_idx.get(sid, set())
        p = pred_idx.get(sid, set())
        for span in g & p:
            tp_t[span[2]] += 1
        for span in p - g:
            fp_t[span[2]] += 1
        for span in g - p:
            fn_t[span[2]] += 1

    tp, fp, fn = sum(tp_t.values()), sum(fp_t.values()), sum(fn_t.values())
    precision, recall, f1 = _prf(tp, fp, fn)

    per_type: dict[str, TypeScore] = {}
    for name in sorted(set(tp_t) | set(fp_t) | set(fn_t)):
        t, f_p, f_n = tp_t[name], fp_t[name], fn_t[name]
        p_t, r_t, f_t = _prf(t, f_p, f_n)
        per_type[name] = TypeScore(p_t, r_t, f_t, support=t + f_n, tp=t, fp=f_p, fn=f_n)

    return ScoreResult(
        precision=precision, recall=recall, f1=f1,
        support=tp + fn, tp=tp, fp=fp, fn=fn, per_type=per_type,
    )
