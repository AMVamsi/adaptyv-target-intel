from target_intel.literature.ner import EntityType, extract_entities


def test_extracts_named_binder():
    entities = extract_entities("Trastuzumab binds domain IV with high affinity.")
    named = [e for e in entities if e.type == EntityType.BINDER_NAMED]
    assert any(e.text.lower() == "trastuzumab" for e in named)


def test_extracts_generic_binder():
    entities = extract_entities("Several nanobody binders were selected.")
    generic = [e for e in entities if e.type == EntityType.BINDER_GENERIC]
    assert any("nanobody" in e.text.lower() for e in generic)


def test_extracts_epitope():
    entities = extract_entities("This antibody binds domain III of the receptor.")
    epitopes = [e for e in entities if e.type == EntityType.EPITOPE]
    assert len(epitopes) == 1
    assert "domain III" in epitopes[0].text


def test_extracts_affinity_range():
    entities = extract_entities("Affinity was reported at 1 to 5 nanomolar.")
    affinities = [e for e in entities if e.type == EntityType.AFFINITY_VALUE]
    assert len(affinities) == 1
    lo, hi = affinities[0].value_range_m
    assert lo == 1e-9
    assert hi == 5e-9


def test_extracts_single_affinity_value():
    entities = extract_entities("Binding affinity of 8 nanomolar was measured.")
    affinities = [e for e in entities if e.type == EntityType.AFFINITY_VALUE]
    assert len(affinities) == 1
    lo, hi = affinities[0].value_range_m
    assert lo == hi == 8e-9


def test_extracts_qualitative_affinity():
    entities = extract_entities("The binder showed sub-nanomolar affinity.")
    affinities = [e for e in entities if e.type == EntityType.AFFINITY_VALUE]
    assert len(affinities) == 1
    lo, hi = affinities[0].value_range_m
    assert lo == 1e-10
    assert hi == 1e-9


def test_extracts_caveat_ceiling():
    entities = extract_entities("Affinities below 100 picomolar are uncommon in this literature.")
    affinities = [e for e in entities if e.type == EntityType.AFFINITY_VALUE]
    assert len(affinities) == 1
    assert affinities[0].text.startswith("caveat:")
    lo, hi = affinities[0].value_range_m
    assert hi == 100e-12


def test_extracts_sparsity_signal():
    entities = extract_entities("To date there are very few reports on this orphan receptor.")
    sparsity = [e for e in entities if e.type == EntityType.SPARSITY_SIGNAL]
    assert len(sparsity) >= 1


def test_no_double_counting_range_and_single():
    # "1 to 5 nanomolar" should not ALSO register two separate single-value hits
    entities = extract_entities("Affinity of 1 to 5 nanomolar was observed.")
    affinities = [e for e in entities if e.type == EntityType.AFFINITY_VALUE]
    assert len(affinities) == 1
