"""
Load the literature knowledge graph into Neo4j, with provenance on every edge.

Ported from the thesis pipeline's Neo4j loader
(`scripts/neo4j/load_defensible_subset.py`), which loads a 14,365-triple
defensible subset with full triple provenance. The same three properties
carry over here, and they are the reason this is a graph rather than a
table:

  - **Provenance.** Every BINDS edge stores the PMIDs it was derived from.
    A claim you cannot trace to a source is not evidence, and the whole
    point of this system is producing verdicts a scientist can check.
  - **Idempotence.** Everything is MERGE, never CREATE, so re-running the
    loader converges instead of duplicating the graph.
  - **Label safety.** Node labels and relationship types cannot be
    parameterised in Cypher - they have to be interpolated into the query
    string, which is an injection surface. Every interpolated identifier is
    checked against a strict allowlist pattern first.

Usage:
    python scripts/neo4j/load_graph.py --all
    python scripts/neo4j/load_graph.py --target comp-her2-human
    python scripts/neo4j/load_graph.py --all --dry-run     # print, don't write
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from target_intel.engine import TargetIntelligenceEngine  # noqa: E402
from target_intel.literature.knowledge_graph import TargetKnowledgeGraph  # noqa: E402

# Labels and relationship types are interpolated, not parameterised, so they
# must be validated. Values are always passed as bound parameters.
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

MERGE_NODE = "MERGE (n:{label} {{id: $id}}) SET n += $props"
MERGE_EDGE = (
    "MATCH (a {{id: $source}}), (b {{id: $target}}) "
    "MERGE (a)-[r:{relation}]->(b) SET r += $props"
)

CONSTRAINTS = [
    "CREATE CONSTRAINT target_id IF NOT EXISTS FOR (n:Target) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT epitope_id IF NOT EXISTS FOR (n:Epitope) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT binder_id IF NOT EXISTS FOR (n:Binder) REQUIRE n.id IS UNIQUE",
]


def _check(identifier: str) -> str:
    if not _SAFE_IDENTIFIER.match(identifier):
        raise ValueError(f"Unsafe Cypher identifier from data: {identifier!r}")
    return identifier


def _clean(props: dict) -> dict:
    """Neo4j rejects null property values; drop them rather than store 'None'."""
    return {k: v for k, v in props.items() if v is not None}


def load_graph(session, kg: TargetKnowledgeGraph) -> tuple[int, int]:
    nodes = edges = 0
    for node in kg.nodes:
        session.run(
            MERGE_NODE.format(label=_check(node.label)),
            id=node.id,
            props=_clean(node.properties),
        )
        nodes += 1
    for edge in kg.edges:
        session.run(
            MERGE_EDGE.format(relation=_check(edge.relation)),
            source=edge.source_id,
            target=edge.target_id,
            props=_clean(edge.properties),
        )
        edges += 1
    return nodes, edges


def qc_checks(session) -> dict:
    """Post-load sanity checks. A loader that reports success without
    verifying what landed is just a script that didn't raise."""
    counts = {
        "targets": "MATCH (n:Target) RETURN count(n) AS c",
        "epitopes": "MATCH (n:Epitope) RETURN count(n) AS c",
        "binders": "MATCH (n:Binder) RETURN count(n) AS c",
        "binds_edges": "MATCH ()-[r:BINDS]->() RETURN count(r) AS c",
        "has_epitope_edges": "MATCH ()-[r:HAS_EPITOPE]->() RETURN count(r) AS c",
    }
    report = {name: session.run(q).single()["c"] for name, q in counts.items()}

    # The check that actually matters: an edge with no PMIDs is an
    # unciteable claim, which this system should never produce.
    report["binds_edges_without_provenance"] = session.run(
        "MATCH ()-[r:BINDS]->() WHERE r.pmids IS NULL OR size(r.pmids) = 0 "
        "RETURN count(r) AS c"
    ).single()["c"]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Load the literature KG into Neo4j")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Load every target in the catalog")
    group.add_argument("--target", help="Load a single target by Foundry target_id")
    parser.add_argument("--dry-run", action="store_true", help="Print Cypher instead of writing")
    parser.add_argument("--uri", default=os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--user", default=os.environ.get("NEO4J_USER", "neo4j"))
    parser.add_argument(
        "--password", default=os.environ.get("NEO4J_PASSWORD", "target-intel-dev")
    )
    args = parser.parse_args()

    engine = TargetIntelligenceEngine(mock=True)
    target_ids = (
        [t.target_id for t in engine.client.list_targets()] if args.all else [args.target]
    )
    graphs = [engine.get_target_context(tid).knowledge_graph for tid in target_ids]

    if args.dry_run:
        for kg in graphs:
            print(f"-- {kg.target_hint}")
            print(kg.to_cypher())
        engine.close()
        return

    try:
        from neo4j import GraphDatabase
    except ImportError:
        sys.exit(
            "The neo4j driver is not installed. It is an optional extra so the "
            "core package stays dependency-light:\n    pip install 'target-intel[neo4j]'"
        )

    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
    try:
        with driver.session() as session:
            for statement in CONSTRAINTS:
                session.run(statement)

            total_nodes = total_edges = 0
            for target_id, kg in zip(target_ids, graphs, strict=True):
                nodes, edges = load_graph(session, kg)
                total_nodes += nodes
                total_edges += edges
                print(f"  {target_id}: {nodes} nodes, {edges} edges")

            print(f"\nLoaded {total_nodes} nodes / {total_edges} edges (MERGE, idempotent)\n")
            print("QC:")
            report = qc_checks(session)
            for name, value in report.items():
                print(f"  {name}: {value}")

            if report["binds_edges_without_provenance"]:
                sys.exit("\nFAIL: BINDS edges exist with no PMID provenance.")
            print("\nAll QC checks passed.")
    finally:
        driver.close()
        engine.close()


if __name__ == "__main__":
    main()
