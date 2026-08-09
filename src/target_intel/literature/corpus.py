"""
Offline literature corpus for demo/eval mode.

IMPORTANT: these are short, originally-written illustrative snippets that
summarize *general, well-established textbook knowledge* about each
target's binder/epitope landscape (e.g. "trastuzumab binds HER2 domain
IV in the sub-nanomolar range" is a widely-cited textbook fact, not a
reproduction of any single paper). They are NOT copied from any real
PubMed abstract, and are labeled `source="demo_corpus"` everywhere they
are used so a reader never mistakes them for a genuine literature pull.

In live mode (`LiteratureClient(mode="live")`), this module is bypassed
entirely in favor of `pubmed.py`, which queries the real NCBI E-utilities
API and runs extraction over real abstract text. The corpus below exists
so the whole pipeline - retrieval, NER, relation extraction, calibration,
KG construction - can be demonstrated and unit-tested with zero network
access and zero copyright risk.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DemoAbstract:
    pmid_placeholder: str  # clearly fake IDs, e.g. "DEMO0001"
    target_hint: str  # matches Target.uniprot_hint
    text: str


DEMO_CORPUS: list[DemoAbstract] = [
    # ---- HER2 / ERBB2 (P04626) — rich, well-studied target ----
    DemoAbstract(
        "DEMO0001", "P04626",
        "Trastuzumab is a humanized monoclonal antibody that binds domain IV "
        "of the HER2/ERBB2 extracellular region with sub-nanomolar affinity, "
        "reported around 0.1 to 5 nanomolar depending on assay format. "
        "Pertuzumab binds a distinct epitope in domain II and is often used "
        "in combination for complementary blockade of HER2 dimerization.",
    ),
    DemoAbstract(
        "DEMO0002", "P04626",
        "Several single-domain (VHH) binders selected against HER2 domain IV "
        "have been reported with affinities in the low nanomolar to "
        "sub-nanomolar range, similar to trastuzumab, supporting domain IV "
        "as a well-validated, highly druggable epitope for small binder "
        "scaffolds.",
    ),
    DemoAbstract(
        "DEMO0003", "P04626",
        "HER2 overexpression drives a subset of breast and gastric cancers; "
        "the domain IV epitope targeted by trastuzumab remains the most "
        "clinically validated binding site, though domain II binders such as "
        "pertuzumab are also well characterized.",
    ),
    # ---- EGFR (P00533) — rich, well-studied target ----
    DemoAbstract(
        "DEMO0004", "P00533",
        "Cetuximab binds domain III of the EGFR extracellular region and "
        "blocks ligand binding, with reported affinities typically in the "
        "1 to 10 nanomolar range across BLI and SPR studies. Necitumumab "
        "and panitumumab bind overlapping or adjacent epitopes with "
        "comparable affinity.",
    ),
    DemoAbstract(
        "DEMO0005", "P00533",
        "Nanobody and scFv binders selected against EGFR domain III "
        "typically fall in the low nanomolar range; affinities below "
        "100 picomolar are uncommon in the published literature for this "
        "epitope and usually reflect avidity effects from multivalent "
        "formats rather than true monovalent 1:1 affinity.",
    ),
    DemoAbstract(
        "DEMO0006", "P00533",
        "EGFR is one of the most extensively targeted receptor tyrosine "
        "kinases in oncology; domain III is the dominant validated epitope, "
        "while domain I and IV binders are comparatively rare in the "
        "literature.",
    ),
    # ---- PD-L1 / CD274 (Q9NZQ7) — rich, immuno-oncology target ----
    DemoAbstract(
        "DEMO0007", "Q9NZQ7",
        "Atezolizumab and durvalumab bind the IgV domain of PD-L1 and block "
        "the PD-1/PD-L1 interaction, with reported affinities in the "
        "sub-nanomolar to low nanomolar range in most published assays.",
    ),
    DemoAbstract(
        "DEMO0008", "Q9NZQ7",
        "Engineered small binder scaffolds against the PD-L1 IgV domain "
        "have been reported with affinities from roughly 0.5 to 20 "
        "nanomolar; the IgV domain is the dominant clinically validated "
        "epitope for checkpoint blockade.",
    ),
    # ---- GPR35 (Q9HC97) — sparse target, orphan-ish GPCR ----
    DemoAbstract(
        "DEMO0009", "Q9HC97",
        "GPR35 is a G-protein-coupled receptor with a small number of "
        "reported small-molecule ligands; to date there are very few "
        "published protein-based (antibody or binder-scaffold) affinity "
        "measurements against GPR35, and no widely cited validated "
        "extracellular epitope comparable to HER2 domain IV or EGFR domain "
        "III.",
    ),
    # ---- CD20 / MS4A1 (P11836) — deliberately conflicting literature.
    # Two independently-written sources describe genuinely different
    # affinity ranges for the same extracellular epitope, because they
    # describe different binding formats (bivalent IgG vs. monovalent
    # nanobody) rather than a data error. This is what EvidenceLevel.
    # CONFLICTING is designed to catch: the ranges don't overlap, so
    # blending them into one min/max would manufacture a false consensus.
    DemoAbstract(
        "DEMO0010", "P11836",
        "Certain full-length IgG antibodies against the target's "
        "extracellular domain bind with affinities reported around 10 to "
        "50 nanomolar in several validated studies.",
    ),
    DemoAbstract(
        "DEMO0011", "P11836",
        "Engineered nanobody and VHH binders against the same "
        "extracellular domain have been reported with sub-nanomolar "
        "affinity, in some cases approaching 100 picomolar, reflecting a "
        "different binding mode than full-length antibodies.",
    ),
]
