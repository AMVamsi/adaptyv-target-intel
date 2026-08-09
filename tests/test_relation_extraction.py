from target_intel.literature.corpus import DEMO_CORPUS
from target_intel.literature.knowledge_graph import build_knowledge_graph
from target_intel.literature.relation_extraction import build_target_claim


def test_her2_claim_has_tier1_hits_and_known_binder():
    claim = build_target_claim("P04626", DEMO_CORPUS)
    assert claim.n_tier1_hits > 0
    assert "trastuzumab" in claim.known_binders
    assert claim.affinity_low_m is not None
    assert claim.raw_confidence > 0


def test_gpr35_claim_is_sparse():
    claim = build_target_claim("Q9HC97", DEMO_CORPUS)
    assert claim.n_tier1_hits == 0
    assert claim.n_tier2_hits == 0
    assert claim.sparsity_votes >= 1
    assert claim.raw_confidence == 0.0


def test_egfr_claim_has_caveat_ceiling():
    claim = build_target_claim("P00533", DEMO_CORPUS)
    assert claim.caveat_ceiling_m is not None
    assert claim.caveat_ceiling_m == 100e-12


def test_unknown_target_hint_yields_empty_claim():
    claim = build_target_claim("NOT_A_REAL_TARGET", DEMO_CORPUS)
    assert claim.n_abstracts == 0
    assert claim.raw_confidence == 0.0
    assert claim.known_binders == set()


def test_knowledge_graph_has_target_binder_epitope_nodes():
    claim = build_target_claim("P04626", DEMO_CORPUS)
    kg = build_knowledge_graph(claim, calibrated_confidence=0.8)
    labels = {n.label for n in kg.nodes}
    assert "Target" in labels
    assert "Binder" in labels
    assert "Epitope" in labels
    binds_edges = [e for e in kg.edges if e.relation == "BINDS"]
    assert len(binds_edges) > 0
    assert all(e.properties["confidence"] == 0.8 for e in binds_edges)


def test_knowledge_graph_cypher_export_is_loadable_text():
    claim = build_target_claim("P04626", DEMO_CORPUS)
    kg = build_knowledge_graph(claim, calibrated_confidence=0.8)
    cypher = kg.to_cypher()
    assert "MERGE (:Target" in cypher
    assert "MERGE (:Binder" in cypher
    assert "BINDS" in cypher


def test_sparse_target_knowledge_graph_still_builds():
    claim = build_target_claim("Q9HC97", DEMO_CORPUS)
    kg = build_knowledge_graph(claim, calibrated_confidence=0.0)
    # No binders known for GPR35, so there should be no BINDS edges, but the
    # target/epitope scaffold should still exist without raising.
    assert any(n.label == "Target" for n in kg.nodes)
    assert not any(e.relation == "BINDS" for e in kg.edges)


def test_kg_edges_carry_the_robust_core_not_the_envelope():
    """A graph edge outlives the run that wrote it, so it gets the same
    range a verdict is judged against - the log-space IQR - and never the
    raw envelope, which on real literature spans orders of magnitude. The
    envelope still travels, under a name that says what it is."""
    from target_intel.literature.relation_extraction import typical_range_m

    claim = build_target_claim("P04626", DEMO_CORPUS)
    kg = build_knowledge_graph(claim, calibrated_confidence=0.8)
    core_low, core_high = typical_range_m(claim.affinity_values_m)

    binds = [e for e in kg.edges if e.relation == "BINDS"]
    assert binds
    for edge in binds:
        assert edge.properties["kd_low_m"] == core_low
        assert edge.properties["kd_high_m"] == core_high
        assert edge.properties["kd_envelope_low_m"] == claim.affinity_low_m
        assert edge.properties["kd_envelope_high_m"] == claim.affinity_high_m
        # Extraction links an affinity to its abstract, never to the binder
        # named in it. The edge must not imply otherwise.
        assert edge.properties["attribution"] == "target_level"


def test_cypher_export_states_what_a_binds_edge_does_not_mean():
    """A HER2 query returns papers discussing cetuximab (anti-EGFR), so the
    export has to say that BINDS means 'co-mentioned in this target's
    literature' before someone loads it into a database and reads it as a
    measured interaction."""
    claim = build_target_claim("P04626", DEMO_CORPUS)
    cypher = build_knowledge_graph(claim, calibrated_confidence=0.8).to_cypher()

    header = [line for line in cypher.splitlines() if line.startswith("//")]
    assert header, "export must carry a provenance header"
    assert "NOT 'this binder was measured against it'" in cypher
    # Header must precede the first statement, or it documents nothing.
    assert cypher.splitlines()[0].startswith("//")
