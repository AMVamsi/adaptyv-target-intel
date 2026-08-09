"""
The real-literature corpus: a frozen snapshot of actual PubMed records.

This is the default literature source. `corpus.py`'s hand-written demo
abstracts remain only as deterministic unit-test fixtures - the numbers a
reviewer sees come from real papers.

Extraction is run **once**, at snapshot build time, against the complete
abstract text, and the resulting spans are stored. Loading therefore
replays a real extraction rather than re-running one over a truncated
excerpt, which would quietly measure something different from what the
pipeline actually does. `scripts/build_literature_snapshot.py` regenerates
it from NCBI.

What the file contains: real PMIDs, real citation metadata, real extracted
spans, and short provenance excerpts. What it deliberately does not
contain: full abstract text, which stays in a gitignored cache rather than
being redistributed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .ner import Entity, EntityType

SNAPSHOT_PATH = Path(__file__).parent / "data" / "literature_snapshot.json"


@dataclass(frozen=True)
class SnapshotAbstract:
    """One real PubMed record.

    Field names mirror `DemoAbstract` so the extraction layer consumes both
    without a branch - `pmid_placeholder` is a real PMID here, and keeping
    the name is what lets one code path serve both corpora.
    """

    pmid_placeholder: str
    target_hint: str
    text: str                                   # provenance excerpt, not the full abstract
    entities: list[Entity] = field(default_factory=list)
    citation: str = ""
    title: str = ""

    @property
    def pmid(self) -> str:
        return self.pmid_placeholder


def _entity(raw: dict) -> Entity:
    value_range = raw.get("value_range_m")
    return Entity(
        type=EntityType(raw["type"]),
        text=raw["text"],
        start=raw["start"],
        end=raw["end"],
        value_range_m=tuple(value_range) if value_range else None,
        qualified=raw.get("qualified", True),
    )


@lru_cache(maxsize=1)
def load_snapshot(path: str | None = None) -> tuple[SnapshotAbstract, ...]:
    """Every record across every target, flattened.

    Cached: the file is read once per process. `build_target_claim` filters
    by `target_hint`, matching how `DEMO_CORPUS` is consumed.
    """
    source = Path(path) if path else SNAPSHOT_PATH
    if not source.exists():
        return ()

    raw = json.loads(source.read_text())
    out: list[SnapshotAbstract] = []
    for target_hint, target in raw["targets"].items():
        for record in target["records"]:
            out.append(
                SnapshotAbstract(
                    pmid_placeholder=record["pmid"],
                    target_hint=target_hint,
                    text=record["excerpt"],
                    entities=[_entity(e) for e in record["entities"]],
                    citation=record.get("citation", f"PMID {record['pmid']}"),
                    title=record.get("title", ""),
                )
            )
    return tuple(out)


@lru_cache(maxsize=1)
def load_identities(path: str | None = None) -> dict[str, dict]:
    """{uniprot_hint: NCBI identity} for every resolved target.

    Resolution happens at snapshot-build time, so this is a dict lookup
    rather than a network call - gene identity does not change between
    runs, and looking it up per verdict would be a request to learn
    something already known.
    """
    source = Path(path) if path else SNAPSHOT_PATH
    if not source.exists():
        return {}
    raw = json.loads(source.read_text())
    return {
        hint: target["ncbi"]
        for hint, target in raw["targets"].items()
        if target.get("ncbi")
    }


def snapshot_metadata(path: str | None = None) -> dict:
    """Provenance for the snapshot: when it was built, what query produced
    it, and how many records survived the evidence filter per target.

    Surfaced through `/health` and the CLI so the corpus behind a verdict is
    inspectable without opening the JSON.
    """
    source = Path(path) if path else SNAPSHOT_PATH
    if not source.exists():
        return {"available": False}

    raw = json.loads(source.read_text())
    return {
        "available": True,
        "built_at": raw["_about"]["built_at"],
        "source": raw["_about"]["source"],
        "n_targets": len(raw["targets"]),
        "n_records": sum(t["n_with_evidence"] for t in raw["targets"].values()),
        "per_target": {
            hint: {
                "name": t["name"],
                "gene_symbol": t["gene_symbol"],
                "n_retrieved": t["n_retrieved"],
                "n_with_evidence": t["n_with_evidence"],
                "ncbi": t.get("ncbi"),
                "query": t["query"],
            }
            for hint, t in raw["targets"].items()
        },
    }
