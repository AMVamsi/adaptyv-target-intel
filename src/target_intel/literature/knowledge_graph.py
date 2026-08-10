"""
A small provenance-tagged knowledge graph built from relation-extraction
claims: (Target)-[:HAS_EPITOPE]->(Epitope), (Binder)-[:BINDS {kd_range,
confidence, tier, pmids}]->(Epitope).

The thesis builds this at real scale in Neo4j (33,000+ confidence-scored
triples over 5,000+ PubMed articles). This project's graph is tiny by
comparison (one target's worth of claims at a time) so it's kept as a
plain in-memory structure - no Neo4j server needed to run the demo - but
`export_cypher()` emits real, loadable Cypher so the exact same triples
could be pushed into Neo4j if this were wired into a running instance,
which is the intended production path.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .relation_extraction import TargetLiteratureClaim, typical_range_m


@dataclass
class KGNode:
    id: str
    label: str  # "Target" | "Binder" | "Epitope"
    properties: dict = field(default_factory=dict)


@dataclass
class KGEdge:
    source_id: str
    target_id: str
    relation: str  # "HAS_EPITOPE" | "BINDS"
    properties: dict = field(default_factory=dict)


@dataclass
class TargetKnowledgeGraph:
    target_hint: str
    nodes: list[KGNode] = field(default_factory=list)
    edges: list[KGEdge] = field(default_factory=list)

    def to_cypher(self) -> str:
        # This text gets read by people and loaded into a database that
        # outlives the run, so the header states what `BINDS` does and does
        # not mean: "named in literature retrieved for this target", which is
        # weaker than "binds this target". A HER2 query returns papers that
        # also discuss cetuximab.
        lines = [
            f"// Target {self.target_hint} - literature-derived, provenance-tagged.",
            "// BINDS = 'this binder was named in literature retrieved for this",
            "//         target', NOT 'this binder was measured against it'.",
            "//         Co-mentioned binders from related targets can appear.",
            "// kd_low_m/kd_high_m describe the TARGET's affinity literature",
            "//         (log-space IQR), not the individual binder - see",
            "//         attribution: 'target_level' on every edge.",
        ]
        for n in self.nodes:
            props = ", ".join(f"{k}: {_cypher_value(v)}" for k, v in n.properties.items())
            lines.append(f'MERGE (:{n.label} {{id: "{n.id}"{", " + props if props else ""}}})')
        for e in self.edges:
            props = ", ".join(f"{k}: {_cypher_value(v)}" for k, v in e.properties.items())
            lines.append(
                f'MATCH (a {{id: "{e.source_id}"}}), (b {{id: "{e.target_id}"}}) '
                f'MERGE (a)-[:{e.relation} {{{props}}}]->(b)'
            )
        return "\n".join(lines)


def _cypher_value(v) -> str:
    if isinstance(v, str):
        return '"' + v.replace('"', '\\"') + '"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_cypher_value(x) for x in v) + "]"
    return str(v)


def build_knowledge_graph(claim: TargetLiteratureClaim, calibrated_confidence: float) -> TargetKnowledgeGraph:
    kg = TargetKnowledgeGraph(target_hint=claim.target_hint)
    target_node_id = f"target::{claim.target_hint}"
    kg.nodes.append(KGNode(target_node_id, "Target", {"uniprot": claim.target_hint}))

    epitope_ids = {}
    for epitope_text in claim.known_epitopes:
        eid = f"epitope::{claim.target_hint}::{epitope_text}"
        epitope_ids[epitope_text] = eid
        kg.nodes.append(KGNode(eid, "Epitope", {"name": epitope_text}))
        kg.edges.append(KGEdge(target_node_id, eid, "HAS_EPITOPE"))

    # If no explicit epitope was extracted, still anchor binders to a
    # generic "unspecified epitope" node so binder evidence isn't lost.
    if not epitope_ids:
        eid = f"epitope::{claim.target_hint}::unspecified"
        epitope_ids["unspecified"] = eid
        kg.nodes.append(KGNode(eid, "Epitope", {"name": "unspecified"}))
        kg.edges.append(KGEdge(target_node_id, eid, "HAS_EPITOPE"))

    pmids = [p.pmid for p in claim.provenance]

    # The same robust core the verdict engine compares against, not the raw
    # envelope: HER2's envelope spans 1e-13 to 1.5e-7 M, which as a stored
    # triple says nothing useful. The envelope is kept under `kd_envelope_*`.
    core_low, core_high = typical_range_m(claim.affinity_values_m)

    for binder in claim.known_binders:
        bid = f"binder::{binder}"
        kg.nodes.append(KGNode(bid, "Binder", {"name": binder}))
        for eid in epitope_ids.values():
            kg.edges.append(KGEdge(bid, eid, "BINDS", {
                "kd_low_m": core_low,
                "kd_high_m": core_high,
                "kd_envelope_low_m": claim.affinity_low_m,
                "kd_envelope_high_m": claim.affinity_high_m,
                "confidence": calibrated_confidence,
                "pmids": pmids,
                # The KD range and PMIDs describe *the target's* literature,
                # not this binder measured on its own - extraction links an
                # affinity to the abstract it appeared in. Consumers can
                # filter on this rather than infer it.
                "attribution": "target_level",
            }))

    return kg
