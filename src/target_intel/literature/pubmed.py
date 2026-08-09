"""
Client for the real NCBI E-utilities API (esearch + efetch), used when the
literature layer runs in `mode="live"`.

Point it at a target's gene symbol and it returns real abstract text ready
to hand to the same NER / relation-extraction pipeline demo mode uses - the
extraction layer cannot tell the difference between a `DemoAbstract` and a
live one, which is the whole point of keeping the corpus behind one shape.

Two things worth calling out, because they are the parts that are easy to
get quietly wrong:

1. **XML, not text.** `retmode=text` returns abstracts as prose blocks with
   no machine-readable PMID attached, so pairing them back to the PMIDs you
   searched for means zipping two lists by position - which silently
   mis-attributes every citation the moment PubMed returns a record without
   an abstract (very common: reviews, editorials, retracted records). Since
   a wrong PMID on a verdict is worse than no verdict, this parses
   `retmode=xml` and reads each PMID out of the record it actually belongs
   to. Records with no abstract are dropped rather than shifted onto their
   neighbour's ID.
2. **Politeness.** NCBI asks non-interactive callers to identify themselves
   and to stay under 3 req/s without an API key (10 with one). This client
   throttles, and sends `tool`/`email` when an email is configured.
"""

from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# Aliases safe to interpolate into a PubMed term: letters, digits, hyphen
# and slash only. Anything with parentheses or quotes is query syntax.
_SAFE_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-/]*$")

# NCBI's documented ceilings: 3 req/s anonymous, 10 req/s with an API key.
_RATE_LIMIT_NO_KEY_S = 0.34
_RATE_LIMIT_WITH_KEY_S = 0.11


@dataclass(frozen=True)
class PubMedAbstract:
    """One real PubMed record.

    Carries citation metadata alongside the text because a verdict that
    cites "PMID 41108118, J Mol Biol 2025" is checkable in a way that a bare
    accession number is not - and the whole point of this system is
    producing conclusions a scientist can go and verify.
    """

    pmid: str
    text: str
    title: str = ""
    journal: str = ""
    year: str = ""
    mesh_terms: tuple[str, ...] = ()

    @property
    def citation(self) -> str:
        bits = [b for b in (self.journal, self.year) if b]
        return f"PMID {self.pmid}" + (f" ({', '.join(bits)})" if bits else "")


class PubMedClient:
    def __init__(
        self,
        tool_name: str = "adaptyv-target-intel",
        email: str | None = None,
        api_key: str | None = None,
        rate_limit_s: float | None = None,
    ):
        self._tool = tool_name
        self._email = email or os.environ.get("NCBI_EMAIL")
        self._api_key = api_key or os.environ.get("NCBI_API_KEY")
        self._rate_limit_s = rate_limit_s if rate_limit_s is not None else (
            _RATE_LIMIT_WITH_KEY_S if self._api_key else _RATE_LIMIT_NO_KEY_S
        )
        self._client = httpx.Client(base_url=EUTILS_BASE, timeout=20.0)
        self._last_call = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self._rate_limit_s:
            time.sleep(self._rate_limit_s - elapsed)
        self._last_call = time.monotonic()

    def _common_params(self) -> dict[str, str]:
        params = {"db": "pubmed", "tool": self._tool}
        if self._email:
            params["email"] = self._email
        if self._api_key:
            params["api_key"] = self._api_key
        return params

    def _request(self, method: str, path: str, payload: dict, attempts: int = 3) -> httpx.Response:
        """One throttled call with a bounded retry. E-utilities returns a
        transient 400/429/5xx under burst load even when the request is
        perfectly well-formed (observed while developing against it), so a
        single failed call is not evidence of a bad query."""
        last_error: Exception | None = None
        for attempt in range(attempts):
            self._throttle()
            try:
                if method == "POST":
                    resp = self._client.post(path, data=payload)
                else:
                    resp = self._client.get(path, params=payload)
                resp.raise_for_status()
                return resp
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(self._rate_limit_s * (2**attempt))
        raise RuntimeError(f"E-utilities {path} failed after {attempts} attempts") from last_error

    def build_query(self, gene_symbol: str, aliases: list[str] | None = None) -> str:
        """Two AND-ed clauses, one for the binder and one for the
        measurement.

        The obvious query - gene AND (binding OR affinity OR antibody) - is
        far too loose: measured against live PubMed it returned 18 ERBB2
        abstracts with zero extractable affinity values, because "antibody"
        alone matches every ADC, diagnostic and clinical-outcome paper on
        the target. Requiring an explicit measurement term as its own
        clause raised that to 4 of 20 abstracts carrying usable affinities
        and 19 of 20 mentioning a binder. Recall of *papers* drops; recall
        of *evidence* goes up, which is the thing that matters here.
        """
        names = [gene_symbol, *(aliases or [])]
        seen, name_clause = set(), []
        for n in names:
            candidate = n.strip()
            key = candidate.upper()
            # The gene symbol itself always goes in. Aliases are filtered,
            # because NCBI's alias list is written for lookup, not for
            # search: ERBB2 carries "NEU" and "NGL", which match unrelated
            # papers on any three-letter coincidence, and "p185(erbB2)",
            # whose parentheses are query syntax to PubMed. Both would cost
            # precision to buy nothing.
            is_alias = candidate != gene_symbol
            if is_alias and (len(candidate) < 4 or not _SAFE_ALIAS.match(candidate)):
                continue
            if candidate and key not in seen:
                seen.add(key)
                name_clause.append(f'"{candidate}"[Title/Abstract]')

        return (
            f"({' OR '.join(name_clause)}) AND "
            '(antibody OR nanobody OR VHH OR scFv OR affibody OR "binding protein") AND '
            '("dissociation constant" OR "binding affinity" OR "surface plasmon resonance" '
            'OR "biolayer interferometry") AND hasabstract'
        )

    def search_target_binder_literature(
        self, gene_symbol: str, aliases: list[str] | None = None, max_results: int = 20
    ) -> list[str]:
        """PMIDs for abstracts likely to carry a measured binder affinity for
        this target (e.g. 'ERBB2', aliases ['HER2'])."""
        resp = self._request(
            "GET",
            "/esearch.fcgi",
            {
                **self._common_params(),
                "term": self.build_query(gene_symbol, aliases),
                "retmax": max_results,
                "retmode": "json",
            },
        )
        return [str(p) for p in resp.json()["esearchresult"].get("idlist", [])]

    def fetch_abstracts(self, pmids: list[str]) -> list[PubMedAbstract]:
        """Fetch and parse abstracts, each keyed to the PMID of the record it
        was actually parsed from (see the module docstring on why this
        matters). Records without abstract text are omitted.

        Sent as POST: NCBI's own guidance is to POST once an id list gets
        long, and a GET puts every PMID in the URL.
        """
        if not pmids:
            return []
        resp = self._request(
            "POST", "/efetch.fcgi", {**self._common_params(), "id": ",".join(pmids), "retmode": "xml"}
        )
        return parse_pubmed_xml(resp.text)

    def close(self) -> None:
        self._client.close()


def parse_pubmed_xml(xml_text: str) -> list[PubMedAbstract]:
    """Split an EFetch PubmedArticleSet into (pmid, text) pairs.

    Kept as a module-level function, separate from the HTTP client, so the
    parsing can be unit-tested against a saved XML sample without any
    network access - the network call is the untestable part, the parsing
    is not, and only one of those two needs a live connection to verify.

    Title and abstract are concatenated because binder/affinity/epitope
    mentions frequently appear in the title alone, and the downstream NER
    scans flat text.
    """
    root = ET.fromstring(xml_text)
    out: list[PubMedAbstract] = []

    for article in root.iter("PubmedArticle"):
        pmid = article.findtext(".//MedlineCitation/PMID")
        if not pmid:
            continue

        title_node = article.find(".//ArticleTitle")
        title = "".join(title_node.itertext()).strip() if title_node is not None else ""

        body = _extract_abstract(article)
        if not body:
            continue

        text = f"{title} {body}".strip() if title else body
        out.append(
            PubMedAbstract(
                pmid=pmid.strip(),
                text=text,
                title=title,
                journal=(article.findtext(".//Journal/ISOAbbreviation") or "").strip(),
                year=_extract_year(article),
                mesh_terms=_extract_mesh_terms(article),
            )
        )

    return out


def _extract_abstract(article: ET.Element) -> str:
    """Join every <AbstractText> block, keeping section labels.

    Ported from the thesis XML parser. Structured abstracts split into
    labelled sections (BACKGROUND / METHODS / RESULTS / CONCLUSIONS), and
    keeping the label preserves the distinction between a number the authors
    measured and one they quote from prior work - a flat concatenation
    throws that away. `itertext()` also recovers inline markup children like
    <i> and <sup> that plain `.text` truncates mid-sentence, the subscript
    in "K<sub>D</sub>" being the case that matters here.
    """
    abstract_el = article.find(".//Abstract")
    if abstract_el is None:
        return ""

    parts: list[str] = []
    for node in abstract_el.findall("AbstractText"):
        text = "".join(node.itertext()).strip()
        if not text:
            continue
        label = node.get("Label", "").strip()
        parts.append(f"{label}: {text}" if label else text)
    return " ".join(parts)


def _extract_mesh_terms(article: ET.Element) -> tuple[str, ...]:
    """MeSH descriptors, for relevance inspection. Ported from the thesis."""
    terms = []
    for heading in article.iter("MeshHeading"):
        for desc in heading.findall("DescriptorName"):
            name = (desc.text or "").strip()
            if name:
                terms.append(name)
    return tuple(terms)


def _extract_year(article: ET.Element) -> str:
    for path in (".//Journal/JournalIssue/PubDate/Year", ".//PubMedPubDate/Year"):
        year = article.findtext(path)
        if year and year.strip():
            return year.strip()
    # Some records carry only a free-text MedlineDate like "2024 Jan-Feb".
    return (article.findtext(".//Journal/JournalIssue/PubDate/MedlineDate") or "").strip()[:4]
