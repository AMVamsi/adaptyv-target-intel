"""
Extraction against the notation real PubMed abstracts actually use.

The bundled demo corpus is written in spelled-out prose ("5 nanomolar"),
which is readable but is NOT how published abstracts write affinities -
they write "KD = 2.3 nM". A gazetteer tuned only on the demo corpus scores
perfectly on the demo corpus and extracts literally nothing from live
PubMed, which is exactly what happened before these cases existed.

The two negative cases matter most. Both are real false positives observed
while running this pipeline against live PubMed output, not hypotheticals:

  - "0.8 nm" is a particle diameter (nanometre), not an affinity. A
    case-insensitive unit match reports it as 0.8 nM and it is off by no
    orders of magnitude at all - it just isn't an affinity, and it would
    silently widen a target's expected range.
  - "2 M NaCl" is a buffer, not a binding constant.
"""

from target_intel.literature.ner import EntityType, extract_entities


def _affinities(text):
    return [e for e in extract_entities(text) if e.type == EntityType.AFFINITY_VALUE]


def test_symbol_notation_kd_equals():
    (aff,) = _affinities("The binder bound with KD = 2.3 nM in a 1:1 fit.")
    assert aff.value_range_m == (2.3e-9, 2.3e-9)


def test_symbol_notation_picomolar():
    (aff,) = _affinities("A high-affinity clone bound at 150 pM.")
    assert aff.value_range_m == (150e-12, 150e-12)


def test_symbol_notation_micromolar_both_micro_signs():
    # U+00B5 MICRO SIGN and U+03BC GREEK SMALL LETTER MU both appear in the
    # wild and are different codepoints; both must resolve to 1e-6.
    micro_sign = _affinities("Weak binding at 1.2 µM was observed.")[0]
    greek_mu = _affinities("Weak binding at 1.2 μM was observed.")[0]
    assert micro_sign.value_range_m == greek_mu.value_range_m == (1.2e-6, 1.2e-6)


def test_hyphenated_symbol_range():
    (aff,) = _affinities("Affinities spanned 0.1-5 nM across the panel.")
    lo, hi = aff.value_range_m
    assert round(lo, 12) == 1e-10
    assert hi == 5e-9


def test_non_breaking_space_between_value_and_unit():
    # PubMed's XML uses U+00A0 here; a plain " " match misses it entirely.
    (aff,) = _affinities("Reported affinity was 0.8 nM by SPR.")
    assert round(aff.value_range_m[0], 12) == 8e-10


def test_symbol_caveat_ceiling():
    (aff,) = _affinities("Affinities below 100 pM are uncommon for this epitope.")
    assert aff.text.startswith("caveat:")
    assert aff.value_range_m[1] == 100e-12


def test_nanometre_is_not_nanomolar():
    """The unit match is case-sensitive on purpose: nm != nM."""
    assert _affinities("Gold particles of 0.8 nm diameter were characterised.") == []


def test_buffer_molarity_is_not_an_affinity():
    """Bare M and mM are excluded - at those concentrations it's a reagent."""
    assert _affinities("Samples were eluted in 2 M NaCl buffered with 10 mM Tris.") == []


def test_spelled_out_notation_still_works():
    """The symbol support is additive - the demo corpus must not regress."""
    (aff,) = _affinities("Binding affinity of 8 nanomolar was measured.")
    assert aff.value_range_m == (8e-9, 8e-9)
