"""
Resolve a target to a canonical NCBI identity.

A Foundry catalog entry says `"HER2 / ERBB2"`. That is a display string,
not an identifier: it doesn't say which organism, which isoform, or which
of the eleven other names the literature uses for the same protein. Every
downstream claim - "these are HER2's published binders", "this KD is
typical for this target" - is only as trustworthy as the answer to "which
protein, exactly".

So each target is resolved against NCBI Gene and pinned to:

  - an **NCBI Gene ID** (stable, unambiguous, cross-referenceable)
  - the **official full name** and organism/taxon
  - the **canonical RefSeq protein accession** (e.g. `NP_004439.2`) with its
    length, which is the identifier a protein engineer would actually use
  - the gene's **registered aliases**

The aliases are not decoration. NCBI lists twelve for ERBB2 - HER2, NEU,
CD340, c-erbB2 among them - and the literature uses all of them. Feeding
those back into the PubMed query recovers papers a symbol-only search
misses, which is a measurable recall gain rather than a cosmetic field.

Resolution runs at snapshot-build time, not per request: gene identity
doesn't change between runs, and a lookup on every verdict would be a
network call to learn something already known.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from .eutils import EUtilsClient

# Curated RefSeq protein accessions. `NP_` is a reviewed protein sequence;
# `XP_` is model-predicted. For a canonical identity only the curated ones
# are appropriate.
_CURATED_PROTEIN = re.compile(r"^NP_\d+")


@dataclass(frozen=True)
class GeneIdentity:
    """A target's canonical NCBI identity."""

    gene_id: str
    symbol: str
    full_name: str = ""
    organism: str = ""
    taxon_id: str = ""
    chromosome: str = ""
    map_location: str = ""
    aliases: tuple[str, ...] = ()
    refseq_protein: str = ""       # e.g. "NP_004439.2"
    protein_name: str = ""         # e.g. "receptor tyrosine-protein kinase erbB-2 isoform a"
    protein_length: int | None = None

    @property
    def gene_url(self) -> str:
        return f"https://www.ncbi.nlm.nih.gov/gene/{self.gene_id}"

    @property
    def protein_url(self) -> str:
        return (
            f"https://www.ncbi.nlm.nih.gov/protein/{self.refseq_protein}"
            if self.refseq_protein else ""
        )

    def as_dict(self) -> dict:
        return {
            "gene_id": self.gene_id,
            "symbol": self.symbol,
            "full_name": self.full_name,
            "organism": self.organism,
            "taxon_id": self.taxon_id,
            "chromosome": self.chromosome,
            "map_location": self.map_location,
            "aliases": list(self.aliases),
            "refseq_protein": self.refseq_protein,
            "protein_name": self.protein_name,
            "protein_length": self.protein_length,
            "gene_url": self.gene_url,
            "protein_url": self.protein_url,
        }


@dataclass
class _ProteinHit:
    uid: str
    accession: str = ""
    title: str = ""
    length: int | None = None
    sort_key: int = field(default=10**9)


class NCBIGeneClient(EUtilsClient):
    """Gene-symbol -> canonical gene and protein identity."""

    default_db = "gene"

    def resolve(self, symbol: str, organism: str = "human") -> GeneIdentity | None:
        """Resolve a gene symbol to its canonical identity, or None.

        Returns None rather than raising when nothing matches: an
        unresolvable target should degrade to "no NCBI identity recorded",
        not break a snapshot build for the four targets that did resolve.
        """
        gene_id = self._search_gene(symbol, organism)
        if not gene_id:
            return None

        identity = self._summarise_gene(gene_id, symbol)
        protein = self._canonical_protein(gene_id)
        if protein is None:
            # Gene resolved but no curated protein: keep the gene identity
            # rather than discarding a good half-answer.
            return identity

        return replace(
            identity,
            refseq_protein=protein.accession,
            protein_name=protein.title,
            protein_length=protein.length,
        )

    # -- steps ----------------------------------------------------------

    def _search_gene(self, symbol: str, organism: str) -> str | None:
        """`[Gene Name]` and `[orgn]` are used rather than a free-text
        search so that "EGFR" cannot match a paper title or an unrelated
        ortholog in another species."""
        payload = {
            **self._common_params("gene"),
            "term": f"{symbol}[Gene Name] AND {organism}[orgn]",
            "retmax": 1,
        }
        ids = self._get_json("/esearch.fcgi", payload)["esearchresult"].get("idlist", [])
        return str(ids[0]) if ids else None

    def _summarise_gene(self, gene_id: str, symbol: str) -> GeneIdentity:
        record = self._get_json(
            "/esummary.fcgi", {**self._common_params("gene"), "id": gene_id}
        )["result"][gene_id]

        organism = record.get("organism") or {}
        aliases = tuple(
            a.strip() for a in (record.get("otheraliases") or "").split(",") if a.strip()
        )

        return GeneIdentity(
            gene_id=gene_id,
            symbol=record.get("name") or symbol,
            full_name=record.get("description", ""),
            organism=organism.get("scientificname", ""),
            taxon_id=str(organism.get("taxid", "")),
            chromosome=record.get("chromosome", ""),
            map_location=record.get("maplocation", ""),
            aliases=aliases,
        )

    def _canonical_protein(self, gene_id: str, max_candidates: int = 200) -> _ProteinHit | None:
        """Pick one representative protein sequence for the gene.

        A gene links to many RefSeq proteins - ERBB2 has 32 - because each
        isoform gets its own accession. Choosing arbitrarily would make the
        recorded identity unstable between runs, so this filters to curated
        `NP_` records and takes the lowest accession number, which is the
        earliest-assigned and conventionally the reference isoform
        (`NP_004439.2` for ERBB2). It is a heuristic, and a deliberately
        legible one: isoform selection is a judgement call that belongs to
        whoever designed the construct, not to a retrieval script.

        The candidate cap has to sit above the number of linked isoforms or
        the "lowest accession" is only lowest among an arbitrary prefix -
        capping at 20 silently returned ERBB2 isoform f, because the
        canonical NP_004439 sat outside the first 20 links.
        """
        linksets = self._get_json(
            "/elink.fcgi",
            {
                **self._common_params("protein"),
                "dbfrom": "gene",
                "id": gene_id,
                "linkname": "gene_protein_refseq",
            },
        ).get("linksets", [])
        if not linksets:
            return None

        uids: list[str] = []
        for db in linksets[0].get("linksetdbs", []):
            uids.extend(str(u) for u in db.get("links", []))
        if not uids:
            return None

        summaries = self._get_json(
            "/esummary.fcgi",
            {**self._common_params("protein"), "id": ",".join(uids[:max_candidates])},
        )["result"]

        best: _ProteinHit | None = None
        for uid in summaries.get("uids", []):
            record = summaries[uid]
            accession = record.get("accessionversion") or record.get("caption", "")
            if not _CURATED_PROTEIN.match(accession):
                continue
            digits = re.search(r"\d+", accession)
            hit = _ProteinHit(
                uid=uid,
                accession=accession,
                title=(record.get("title") or "").split(" [")[0],
                length=record.get("slen"),
                sort_key=int(digits.group()) if digits else 10**9,
            )
            if best is None or hit.sort_key < best.sort_key:
                best = hit
        return best
