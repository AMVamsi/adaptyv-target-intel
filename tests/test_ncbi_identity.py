"""
Canonical NCBI grounding for each target.

A Foundry catalog entry says `"HER2 / ERBB2"` - a display string, not an
identifier. It doesn't say which organism, which isoform, or which of the
dozen other names the literature uses. Every downstream claim is only as
trustworthy as the answer to "which protein, exactly", so each target is
pinned to an NCBI Gene ID and a curated RefSeq protein accession.

These tests are offline: resolution runs at snapshot-build time and the
result ships in the repo. The parts that need a network (the E-utilities
calls) are exercised by rebuilding the snapshot, not by the suite - a test
that hits NCBI would be flaky by construction.
"""

from __future__ import annotations

import pytest

from target_intel.engine import TargetIntelligenceEngine
from target_intel.literature.pubmed import PubMedClient
from target_intel.literature.snapshot import load_identities, snapshot_metadata

# Canonical human accessions, verified against NCBI.
EXPECTED = {
    "P04626": ("2064", "ERBB2", "NP_004439.2", 1255),
    "P00533": ("1956", "EGFR", "NP_005219.2", 1210),
    "Q9NZQ7": ("29126", "CD274", "NP_054862.1", 290),
    "Q9HC97": ("2859", "GPR35", "NP_005292.2", 309),
    "P11836": ("931", "MS4A1", "NP_068769.2", 297),
}


def test_every_target_is_pinned_to_a_canonical_identity():
    identities = load_identities()
    assert set(identities) == set(EXPECTED)


@pytest.mark.parametrize("hint,expected", EXPECTED.items())
def test_identity_matches_the_canonical_ncbi_record(hint, expected):
    gene_id, symbol, accession, length = expected
    record = load_identities()[hint]

    assert record["gene_id"] == gene_id
    assert record["symbol"] == symbol
    assert record["refseq_protein"] == accession
    assert record["protein_length"] == length


def test_identities_are_human():
    """`[orgn]` is in the resolution query specifically so that a symbol
    can't silently match an ortholog in another species."""
    for record in load_identities().values():
        assert record["organism"] == "Homo sapiens"
        assert record["taxon_id"] == "9606"


def test_accessions_are_curated_not_predicted():
    """NP_ is a reviewed RefSeq protein; XP_ is model-predicted. A canonical
    identity should never be a prediction."""
    for record in load_identities().values():
        assert record["refseq_protein"].startswith("NP_")


def test_identity_carries_a_resolvable_url():
    record = load_identities()["P04626"]
    assert record["gene_url"].endswith("/gene/2064")
    assert record["protein_url"].endswith("/protein/NP_004439.2")


def test_engine_attaches_identity_to_target_context():
    engine = TargetIntelligenceEngine(mock=True, literature_mode="snapshot")
    ctx = engine.get_target_context("comp-her2-human")
    engine.close()

    assert ctx.ncbi is not None
    assert ctx.ncbi["refseq_protein"] == "NP_004439.2"
    assert ctx.ncbi["full_name"] == "erb-b2 receptor tyrosine kinase 2"


def test_snapshot_metadata_exposes_identity_per_target():
    per_target = snapshot_metadata()["per_target"]
    assert per_target["P04626"]["ncbi"]["gene_id"] == "2064"


# --- aliases feeding back into retrieval ---------------------------------


def test_ncbi_aliases_reach_the_pubmed_query():
    """The point of collecting aliases: the literature says HER2, not
    ERBB2, and a symbol-only query misses those papers."""
    query = snapshot_metadata()["per_target"]["P04626"]["query"]
    assert '"HER2"[Title/Abstract]' in query
    assert '"CD340"[Title/Abstract]' in query


def test_ambiguous_short_aliases_are_filtered_out():
    """ERBB2 carries "NEU" and "NGL". Three-letter aliases match unrelated
    papers on coincidence, which costs precision to buy nothing."""
    query = PubMedClient().build_query("ERBB2", aliases=["HER2", "NEU", "NGL"])
    assert '"HER2"[Title/Abstract]' in query
    assert '"NEU"' not in query
    assert '"NGL"' not in query


def test_aliases_containing_query_syntax_are_rejected():
    """"p185(erbB2)" is a real NCBI alias whose parentheses PubMed reads as
    grouping - interpolating it produces a malformed query, not a match."""
    query = PubMedClient().build_query("ERBB2", aliases=["p185(erbB2)"])
    assert "p185" not in query


def test_the_gene_symbol_itself_is_never_filtered():
    """A short symbol is still the primary term - filtering applies to
    aliases only."""
    assert '"NGF"[Title/Abstract]' in PubMedClient().build_query("NGF")
