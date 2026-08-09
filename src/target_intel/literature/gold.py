"""
Loads the hand-labeled span gold standard and scores the extractor on it.

Gold spans are stored as `(text, occurrence)` rather than raw character
offsets. Hand-writing offsets is the classic way to ship a silently wrong
gold set: one edited sentence shifts every span after it and the corpus
still parses fine, so the metric drifts with no error anywhere. Resolving
offsets at load time and asserting the recovered substring matches makes
that failure loud instead of silent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .ner import Entity, EntityType, extract_entities
from .scorer import ScoreResult, score_spans

GOLD_PATH = Path(__file__).parent / "data" / "ner_gold.json"

# SPARSITY_SIGNAL is excluded: it's a discourse cue, not a span two
# annotators would agree the boundaries of. Scoring an unannotated type
# against an empty gold set reports 0.0 precision for a reason unrelated to
# quality - see scorer.score_spans.
EVALUATED_TYPES: tuple[EntityType, ...] = (
    EntityType.BINDER_NAMED,
    EntityType.BINDER_GENERIC,
    EntityType.EPITOPE,
    EntityType.AFFINITY_VALUE,
)


@dataclass(frozen=True)
class GoldSentence:
    id: str
    split: str
    text: str
    entities: list[Entity]


class GoldSetError(ValueError):
    """Raised when a labeled span cannot be located in its sentence."""


def _resolve(sentence_id: str, text: str, span_text: str, occurrence: int) -> tuple[int, int]:
    start = -1
    for _ in range(occurrence + 1):
        start = text.find(span_text, start + 1)
        if start == -1:
            raise GoldSetError(
                f"{sentence_id}: could not find occurrence {occurrence} of {span_text!r}"
            )
    end = start + len(span_text)
    if text[start:end] != span_text:  # pragma: no cover - defensive
        raise GoldSetError(f"{sentence_id}: span {span_text!r} did not round-trip")
    return start, end


def load_gold(path: Path = GOLD_PATH) -> list[GoldSentence]:
    raw = json.loads(path.read_text())
    sentences: list[GoldSentence] = []

    for item in raw["sentences"]:
        text = item["text"]
        entities = []
        for ent in item["entities"]:
            start, end = _resolve(item["id"], text, ent["text"], ent.get("n", 0))
            entities.append(
                Entity(type=EntityType(ent["type"].lower()), text=ent["text"], start=start, end=end)
            )
        sentences.append(
            GoldSentence(id=item["id"], split=item["split"], text=text, entities=entities)
        )

    return sentences


def score_extractor(split: str | None = None, path: Path = GOLD_PATH) -> ScoreResult:
    """Run the live extractor over the gold sentences and score it.

    `split=None` scores everything; pass "demo_corpus" or
    "heldout_realistic" to score one split. The two are worth reading
    separately - the gazetteer was written against the demo corpus, so that
    split is an in-domain ceiling, not evidence of generalization.
    """
    sentences = [s for s in load_gold(path) if split is None or s.split == split]

    predictions = {s.id: extract_entities(s.text) for s in sentences}
    gold = {s.id: s.entities for s in sentences}
    return score_spans(predictions, gold, types=EVALUATED_TYPES)


def score_report(path: Path = GOLD_PATH) -> dict:
    """Overall plus per-split scores, with the sentence counts attached.

    The counts ship with the numbers on purpose: an F1 quoted without its
    corpus size is the same failure mode as an ECE quoted without n.
    """
    sentences = load_gold(path)
    splits = sorted({s.split for s in sentences})

    return {
        "gold_sentences": len(sentences),
        "gold_spans": sum(len(s.entities) for s in sentences),
        "types_evaluated": [t.value for t in EVALUATED_TYPES],
        "types_not_evaluated": ["sparsity_signal"],
        "overall": score_extractor(path=path).as_dict(),
        "by_split": {
            split: {
                "n_sentences": sum(1 for s in sentences if s.split == split),
                **score_extractor(split=split, path=path).as_dict(),
            }
            for split in splits
        },
    }
