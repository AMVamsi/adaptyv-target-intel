"""
Build the real-literature snapshot from live PubMed.

Why a snapshot rather than either extreme:

  - Querying PubMed on every run makes the demo dependent on a network, on
    NCBI being up, and on results that drift between runs. A verdict that
    changes because a new paper was indexed this morning is not something
    you can put in front of a reviewer.
  - Shipping full abstract text in the repo redistributes publisher-
    copyrighted material.

So the repo carries a **derived** snapshot: real PMIDs, real citation
metadata, real extracted spans, and short provenance excerpts - everything
needed to produce verdicts offline, and nothing that redistributes a full
abstract. Full text is cached under `.cache/` (gitignored) for re-running
extraction, and this script rebuilds it from NCBI on demand.

That is the same discipline the thesis pipeline uses: raw corpora
gitignored, processed splits reproducible from raw via a script.

Usage:
    python scripts/build_literature_snapshot.py            # rebuild from PubMed
    python scripts/build_literature_snapshot.py --dry-run  # show, don't write
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from target_intel.literature.ncbi import NCBIGeneClient  # noqa: E402
from target_intel.literature.ner import EntityType, extract_entities  # noqa: E402
from target_intel.literature.pubmed import PubMedClient  # noqa: E402
from target_intel.sdk import AdaptyvClient  # noqa: E402

SNAPSHOT_PATH = ROOT / "src" / "target_intel" / "literature" / "data" / "literature_snapshot.json"
CACHE_DIR = ROOT / ".cache" / "abstracts"

# Excerpt length for provenance. Short enough to be a citation pointer
# rather than a redistribution of the abstract.
EXCERPT_CHARS = 200


def _excerpt(text: str, entities) -> str:
    """A short window around the first extracted affinity or binder, so the
    stored excerpt shows the evidence rather than the first 200 characters
    of boilerplate about study design."""
    anchors = [e for e in entities if e.type is EntityType.AFFINITY_VALUE] or list(entities)
    if not anchors:
        return text[:EXCERPT_CHARS].strip()
    centre = min(anchors, key=lambda e: e.start).start
    start = max(0, centre - EXCERPT_CHARS // 2)
    return text[start:start + EXCERPT_CHARS].strip()


def build(dry_run: bool = False) -> dict:
    client = AdaptyvClient(mock=True)
    targets = client.list_targets()
    client.close()

    pubmed = PubMedClient()
    genes = NCBIGeneClient()
    snapshot: dict = {
        "_about": {
            "source": "Real PubMed records retrieved via NCBI E-utilities.",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "contains": (
                "Real PMIDs, citation metadata, extracted entity spans and short "
                f"(<={EXCERPT_CHARS} char) provenance excerpts. Full abstract text is NOT "
                "redistributed here; it is cached under .cache/ (gitignored) and "
                "re-fetchable with scripts/build_literature_snapshot.py."
            ),
            "excerpt_chars": EXCERPT_CHARS,
        },
        "targets": {},
    }

    try:
        for target in targets:
            if not target.gene_symbol:
                continue

            # Pin the target to a canonical NCBI identity before searching.
            # This does two jobs: it records *which protein exactly* every
            # downstream claim is about, and it hands back the gene's
            # registered aliases - which the literature actually uses and a
            # symbol-only query would miss.
            identity = genes.resolve(target.gene_symbol)

            aliases = [p.strip() for p in target.name.replace("/", " ").split() if p.strip()]
            if identity:
                aliases.extend(identity.aliases)
            query = pubmed.build_query(target.gene_symbol, aliases=aliases)
            pmids = pubmed.search_target_binder_literature(target.gene_symbol, aliases=aliases)
            abstracts = pubmed.fetch_abstracts(pmids)

            records = []
            for abstract in abstracts:
                entities = extract_entities(abstract.text)
                # Keep only records that actually carry extractable evidence.
                # A record with no binder and no affinity contributes nothing
                # to a prior, and storing it would pad the corpus with
                # abstracts that only look like coverage.
                has_affinity = any(e.type is EntityType.AFFINITY_VALUE and e.qualified for e in entities)
                has_binder = any(
                    e.type in (EntityType.BINDER_NAMED, EntityType.BINDER_GENERIC) for e in entities
                )
                if not (has_affinity or has_binder):
                    continue

                records.append({
                    "pmid": abstract.pmid,
                    "title": abstract.title,
                    "journal": abstract.journal,
                    "year": abstract.year,
                    "citation": abstract.citation,
                    "excerpt": _excerpt(abstract.text, entities),
                    "has_affinity": has_affinity,
                    "has_binder": has_binder,
                    "entities": [
                        {
                            "type": e.type.value,
                            "text": e.text,
                            "start": e.start,
                            "end": e.end,
                            "value_range_m": list(e.value_range_m) if e.value_range_m else None,
                            "qualified": e.qualified,
                        }
                        for e in entities
                    ],
                })

                if not dry_run:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    (CACHE_DIR / f"{abstract.pmid}.txt").write_text(abstract.text)

            snapshot["targets"][target.uniprot_hint] = {
                "target_id": target.target_id,
                "name": target.name,
                "gene_symbol": target.gene_symbol,
                "uniprot_hint": target.uniprot_hint,
                "ncbi": identity.as_dict() if identity else None,
                "query": query,
                "n_retrieved": len(abstracts),
                "n_with_evidence": len(records),
                "records": records,
            }
            pinned = (
                f"{identity.refseq_protein or 'gene ' + identity.gene_id}"
                if identity else "unresolved"
            )
            print(
                f"  {target.name:16} {pinned:16} {len(abstracts):>3} retrieved -> "
                f"{len(records):>3} with extractable evidence"
            )
    finally:
        pubmed.close()
        genes.close()

    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild the real-literature snapshot")
    parser.add_argument("--dry-run", action="store_true", help="Print summary, write nothing")
    args = parser.parse_args()

    print("Querying PubMed (real network calls, throttled to NCBI's rate limit)...\n")
    snapshot = build(dry_run=args.dry_run)

    total = sum(t["n_with_evidence"] for t in snapshot["targets"].values())
    print(f"\n{total} records with extractable evidence across {len(snapshot['targets'])} targets")

    if args.dry_run:
        print("(dry run - nothing written)")
        return

    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2))
    print(f"Wrote {SNAPSHOT_PATH}")
    print(f"Cached full text under {CACHE_DIR} (gitignored)")


if __name__ == "__main__":
    main()
