"""
Live-literature path, tested without a live connection.

The network call is untestable offline; the parsing and query construction
are not, and those are where the real bugs were. `tests/data/
efetch_sample.xml` is a genuine, unedited EFetch response for two real
PMIDs, saved so the parser can be exercised against PubMed's actual XML -
including its structured abstracts and inline markup - on a machine with
no network access.

No test in this file opens a socket.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from target_intel.literature.ner import EntityType, extract_entities
from target_intel.literature.pubmed import PubMedClient, parse_pubmed_xml

SAMPLE = (Path(__file__).parent / "data" / "efetch_sample.xml").read_text()


def test_parses_real_efetch_xml_into_pmid_keyed_abstracts():
    abstracts = parse_pubmed_xml(SAMPLE)
    assert len(abstracts) == 2
    for a in abstracts:
        assert a.pmid.isdigit()
        assert len(a.text) > 200


def test_each_abstract_keeps_its_own_pmid():
    """The bug this guards: pairing abstracts to PMIDs by list position.

    Positional zipping only stays correct while every requested record has
    an abstract. The moment one doesn't, every citation after it shifts by
    one and the system confidently cites the wrong paper.
    """
    abstracts = parse_pubmed_xml(SAMPLE)
    by_pmid = {a.pmid: a.text for a in abstracts}
    assert set(by_pmid) == {"41108118", "40849046"}
    # Each record's text must come from its own <PubmedArticle> element.
    for pmid, text in by_pmid.items():
        assert pmid in SAMPLE.split(text[:60])[0]


def test_records_without_abstracts_are_dropped_not_shifted():
    xml = """<PubmedArticleSet>
      <PubmedArticle><MedlineCitation><PMID>111</PMID>
        <Article><ArticleTitle>No abstract here</ArticleTitle></Article>
      </MedlineCitation></PubmedArticle>
      <PubmedArticle><MedlineCitation><PMID>222</PMID>
        <Article><ArticleTitle>Has one</ArticleTitle>
          <Abstract><AbstractText>Body text.</AbstractText></Abstract>
        </Article>
      </MedlineCitation></PubmedArticle>
    </PubmedArticleSet>"""
    abstracts = parse_pubmed_xml(xml)
    assert [a.pmid for a in abstracts] == ["222"]
    assert "Body text." in abstracts[0].text


def test_structured_abstract_sections_are_joined():
    xml = """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>333</PMID>
      <Article><ArticleTitle>T</ArticleTitle><Abstract>
        <AbstractText Label="BACKGROUND">Alpha.</AbstractText>
        <AbstractText Label="RESULTS">Bound at 2.5 nM.</AbstractText>
      </Abstract></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""
    (abstract,) = parse_pubmed_xml(xml)
    assert "Alpha." in abstract.text and "2.5 nM" in abstract.text


def test_inline_markup_does_not_break_a_sentence():
    """<i>/<sup> children mid-sentence are dropped by .text but not itertext()."""
    xml = """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>444</PMID>
      <Article><ArticleTitle>T</ArticleTitle><Abstract>
        <AbstractText>The K<sub>D</sub> was 2 nM.</AbstractText>
      </Abstract></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""
    (abstract,) = parse_pubmed_xml(xml)
    assert "2 nM" in abstract.text


def test_extraction_runs_on_real_abstract_text():
    """End of the live path: real PubMed text must yield real entities.

    Before symbol-notation support this returned nothing at all on live
    text while scoring perfectly on the demo corpus.
    """
    abstracts = parse_pubmed_xml(SAMPLE)
    joined = " ".join(a.text for a in abstracts)
    kinds = {e.type for e in extract_entities(joined)}
    assert EntityType.AFFINITY_VALUE in kinds
    assert EntityType.BINDER_NAMED in kinds or EntityType.BINDER_GENERIC in kinds


@pytest.mark.parametrize("field", ["dissociation constant", "hasabstract", "nanobody"])
def test_query_requires_a_measurement_clause(field):
    query = PubMedClient(email=None).build_query("ERBB2", aliases=["HER2"])
    assert field in query


def test_query_includes_aliases_without_duplicating_the_symbol():
    query = PubMedClient().build_query("ERBB2", aliases=["HER2", "erbb2", "ERBB2"])
    assert '"ERBB2"[Title/Abstract]' in query
    assert '"HER2"[Title/Abstract]' in query
    assert query.count("[Title/Abstract]") == 2  # case-insensitive dedupe
