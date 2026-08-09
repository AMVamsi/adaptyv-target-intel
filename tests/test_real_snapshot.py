"""
The real-literature snapshot, and the two mechanisms that make it usable.

Everything the default mode reports comes from real PubMed records, so
these tests assert against real PMIDs and real extracted values. They are
offline: the snapshot ships in the repo, and rebuilding it (which does hit
the network) is a separate script.

Two behaviours here exist only because real literature broke the naive
version, and both would regress silently:

  - **Affinity qualification.** A number with molar units is not an
    affinity. Real abstracts are full of IC50s, working concentrations and
    doses; admitting them made HER2's prior span five orders of magnitude.
  - **The robust core.** Even correctly-extracted affinities span a huge
    range, because published binders run from engineered sub-picomolar
    constructs to weak starting clones. The min/max envelope is real and
    useless; the log-space IQR is what a plausibility check can use.
"""

from __future__ import annotations

from target_intel.engine import TargetIntelligenceEngine
from target_intel.interpretation import LiteratureDensity
from target_intel.literature.ner import EntityType, extract_entities
from target_intel.literature.relation_extraction import EvidenceLevel, typical_range_m
from target_intel.literature.snapshot import load_snapshot, snapshot_metadata


def _engine():
    return TargetIntelligenceEngine(mock=True, literature_mode="snapshot")


# --- the snapshot itself -------------------------------------------------


def test_snapshot_is_present_and_non_trivial():
    records = load_snapshot()
    assert len(records) > 50


def test_every_record_carries_a_real_numeric_pmid():
    """Real PMIDs, not DEMO#### placeholders - the citations are checkable."""
    for record in load_snapshot():
        assert record.pmid.isdigit(), record.pmid


def test_records_carry_citation_metadata():
    """A bare accession is far less useful than journal and year."""
    records = load_snapshot()
    assert sum(1 for r in records if r.citation and r.title) > len(records) * 0.8


def test_snapshot_metadata_reports_the_query_that_produced_it():
    meta = snapshot_metadata()
    assert meta["available"]
    assert meta["n_records"] > 50
    for target in meta["per_target"].values():
        assert "dissociation constant" in target["query"]


def test_excerpts_are_short_enough_to_be_citations_not_reproductions():
    for record in load_snapshot():
        assert len(record.text) <= 260


# --- affinity qualification ---------------------------------------------


def test_ic50_is_not_treated_as_an_affinity():
    (value,) = [e for e in extract_entities("The compound showed an IC50 of 45 nM.")
                if e.type is EntityType.AFFINITY_VALUE]
    assert value.qualified is False


def test_working_concentration_is_not_treated_as_an_affinity():
    (value,) = [e for e in extract_entities("Cells were treated with 120 nM inhibitor.")
                if e.type is EntityType.AFFINITY_VALUE]
    assert value.qualified is False


def test_a_stated_dissociation_constant_is_qualified():
    (value,) = [e for e in extract_entities("The nanobody bound with a KD of 2.3 nM.")
                if e.type is EntityType.AFFINITY_VALUE]
    assert value.qualified is True


def test_unqualified_values_are_still_returned_as_spans():
    """Qualification is a belief flag, not a filter - the scorer measures
    span detection, and silently dropping spans would conflate 'found the
    number' with 'believed the number'."""
    values = [e for e in extract_entities("An IC50 of 45 nM was measured.")
              if e.type is EntityType.AFFINITY_VALUE]
    assert len(values) == 1


# --- robust core range ---------------------------------------------------


def test_typical_range_ignores_a_single_extreme_outlier():
    values = [1e-9, 2e-9, 3e-9, 4e-9, 1e-3]
    low, high = typical_range_m(values)
    assert high < 1e-7  # the millimolar outlier must not stretch the core


def test_typical_range_falls_back_to_the_envelope_when_too_few_values():
    """Quartiles of three points are not a distribution."""
    assert typical_range_m([1e-9, 1e-8]) == (1e-9, 1e-8)


def test_typical_range_handles_no_values():
    assert typical_range_m([]) == (None, None)


def test_core_is_strictly_narrower_than_the_envelope_on_real_her2():
    engine = _engine()
    prior = engine.get_target_context("comp-her2-human").prior
    engine.close()

    assert prior.envelope_low_m < prior.low_m
    assert prior.high_m < prior.envelope_high_m


# --- priors derived from real data --------------------------------------


def test_her2_has_real_citations_and_a_plausible_core_range():
    engine = _engine()
    prior = engine.get_target_context("comp-her2-human").prior
    engine.close()

    assert all(p.isdigit() for p in prior.pmids)
    assert prior.density is LiteratureDensity.RICH
    # HER2 binders are published from sub-picomolar to double-digit
    # nanomolar; a core outside that bracket means extraction has drifted.
    assert 1e-13 < prior.low_m < 1e-8
    assert 1e-12 < prior.high_m < 1e-6


def test_gpr35_has_no_binder_literature_at_all():
    """The sparse-target case, on real data: the tightened query returns
    nothing for GPR35, which is why it reads MISSING rather than thin."""
    engine = _engine()
    prior = engine.get_target_context("comp-gpr35-human").prior
    engine.close()

    assert prior.n_abstracts == 0
    assert prior.density is LiteratureDensity.SPARSE
    assert prior.evidence_level is EvidenceLevel.MISSING
    assert prior.low_m is None


def test_density_reflects_evidence_not_confidence():
    """Density counted off the calibrated confidence labelled every real
    target sparse, HER2 included. It counts quantitative sources now."""
    engine = _engine()
    her2 = engine.get_target_context("comp-her2-human").prior
    gpr35 = engine.get_target_context("comp-gpr35-human").prior
    engine.close()

    assert her2.n_quantitative_sources >= 3
    assert gpr35.n_quantitative_sources == 0


def test_confidence_orders_well_studied_above_sparse_targets():
    engine = _engine()
    her2 = engine.get_target_context("comp-her2-human").prior
    gpr35 = engine.get_target_context("comp-gpr35-human").prior
    engine.close()

    assert her2.calibrated_confidence > gpr35.calibrated_confidence
