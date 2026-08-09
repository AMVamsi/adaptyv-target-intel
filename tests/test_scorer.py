"""
Exact-span scoring, and the gold set's own integrity.

Two things are being pinned here. The first is the scorer's arithmetic -
easy to get subtly wrong, and wrong in a direction that flatters the
system. The second is the gold set itself: it stores spans as (text,
occurrence) and resolves offsets at load, so a mis-typed label must fail
loudly rather than silently shifting every subsequent span and quietly
moving the headline F1.
"""

from __future__ import annotations

import pytest

from target_intel.literature.gold import (
    EVALUATED_TYPES,
    GoldSetError,
    load_gold,
    score_extractor,
    score_report,
)
from target_intel.literature.ner import Entity, EntityType
from target_intel.literature.scorer import score_spans


def _ent(start, end, type_=EntityType.BINDER_NAMED):
    return Entity(type=type_, text="x", start=start, end=end)


def test_perfect_match_scores_one():
    spans = {"s1": [_ent(0, 5), _ent(10, 15)]}
    result = score_spans(spans, spans)
    assert result.f1 == 1.0
    assert (result.tp, result.fp, result.fn) == (2, 0, 0)


def test_partial_overlap_is_not_a_match():
    """Exact-span means exact: off-by-one boundaries are a miss both ways."""
    result = score_spans({"s1": [_ent(0, 6)]}, {"s1": [_ent(0, 5)]})
    assert (result.tp, result.fp, result.fn) == (0, 1, 1)
    assert result.f1 == 0.0


def test_right_span_wrong_type_counts_twice():
    """Once as a false positive, once as a false negative - not as a hit."""
    result = score_spans(
        {"s1": [_ent(0, 5, EntityType.BINDER_GENERIC)]},
        {"s1": [_ent(0, 5, EntityType.BINDER_NAMED)]},
    )
    assert (result.tp, result.fp, result.fn) == (0, 1, 1)


def test_spans_do_not_leak_across_sentences():
    """Identical offsets in different sentences must not match each other."""
    result = score_spans({"s1": [_ent(0, 5)]}, {"s2": [_ent(0, 5)]})
    assert (result.tp, result.fp, result.fn) == (0, 1, 1)


def test_empty_prediction_scores_zero_not_an_error():
    result = score_spans({"s1": []}, {"s1": [_ent(0, 5)]})
    assert result.f1 == 0.0
    assert result.fn == 1


def test_type_filter_excludes_unevaluated_types():
    """Scoring an unannotated type against empty gold would report 0.0
    precision for a reason unrelated to quality."""
    preds = {"s1": [_ent(0, 5, EntityType.SPARSITY_SIGNAL), _ent(6, 9, EntityType.EPITOPE)]}
    gold = {"s1": [_ent(6, 9, EntityType.EPITOPE)]}
    assert score_spans(preds, gold, types=(EntityType.EPITOPE,)).f1 == 1.0
    # Without the filter the sparsity span is a false positive.
    assert score_spans(preds, gold).f1 < 1.0


def test_micro_pools_across_types_rather_than_averaging():
    """One rare type must not swing the headline number, which is exactly
    what a type-averaged macro would let it do."""
    preds = {"s1": [_ent(i * 10, i * 10 + 5) for i in range(9)]}
    gold = {
        "s1": [_ent(i * 10, i * 10 + 5) for i in range(9)]
        + [_ent(200, 205, EntityType.EPITOPE)]
    }
    result = score_spans(preds, gold)
    assert result.tp == 9 and result.fn == 1
    assert result.f1 == pytest.approx(2 * 1.0 * 0.9 / 1.9)


# --- gold set integrity -------------------------------------------------


def test_every_gold_span_resolves_to_its_own_text():
    for sentence in load_gold():
        for entity in sentence.entities:
            assert sentence.text[entity.start:entity.end] == entity.text


def test_a_mislabeled_span_fails_loudly(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        '{"sentences": [{"id": "x", "split": "s", "text": "abc",'
        ' "entities": [{"text": "zzz", "type": "EPITOPE"}]}]}'
    )
    with pytest.raises(GoldSetError):
        load_gold(bad)


def test_gold_set_covers_every_evaluated_type():
    labeled = {e.type for s in load_gold() for e in s.entities}
    assert set(EVALUATED_TYPES) <= labeled


def test_gold_set_includes_negative_sentences():
    """Sentences with no entities are what catch the nm/nM and buffer-molarity
    false positives - a gold set of only positives can't."""
    assert any(not s.entities for s in load_gold())


def test_heldout_split_contains_binders_outside_the_gazetteer():
    """The held-out split is only an honest generalization estimate if it
    contains binders the gazetteer was not built around."""
    from target_intel.literature.ner import _NAMED_BINDERS

    heldout_named = {
        e.text.lower()
        for s in load_gold() if s.split == "heldout_realistic"
        for e in s.entities if e.type is EntityType.BINDER_NAMED
    }
    assert heldout_named - set(_NAMED_BINDERS)


def test_extractor_scores_are_reported_with_their_sample_size():
    report = score_report()
    assert report["gold_sentences"] > 0
    assert report["overall"]["micro"]["support"] == report["gold_spans"]
    assert set(report["by_split"]) == {"demo_corpus", "heldout_realistic"}


def test_heldout_recall_is_not_silently_perfect():
    """A gazetteer cannot know binders it doesn't list. If this ever hits
    1.0, the held-out split has stopped being held out."""
    assert score_extractor(split="heldout_realistic").recall < 1.0
