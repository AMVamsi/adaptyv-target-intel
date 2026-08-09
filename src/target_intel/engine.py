"""
TargetIntelligenceEngine: the single object that ties together

    AdaptyvClient (SDK)  ->  literature grounding  ->  interpretation

It is the thing the MCP server, the CLI, and the dashboard generator all
call into, so there is one implementation of "what does it mean to
interpret a result" rather than three copies drifting apart.

A mapping from Foundry target_id -> the target's UniProt-style hint (used
to key literature) lives here rather than in the SDK, since it's specific
to this project's literature-grounding concern, not to the Foundry API
itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from .interpretation import (
    CalibrationStatus,
    CoverageNote,
    ExpectedAffinityPrior,
    Verdict,
    build_prior,
    epitope_diversity_note,
    interpret_result,
)
from .literature import (
    DEMO_CORPUS,
    TargetKnowledgeGraph,
    TargetLiteratureClaim,
    build_knowledge_graph,
    build_target_claim,
    fit_calibrator_on_golden_set,
    load_identities,
    load_snapshot,
)
from .sdk import AdaptyvClient
from .sdk.models import ResultRecord, Target


@dataclass
class TargetContext:
    target: Target
    claim: TargetLiteratureClaim
    prior: ExpectedAffinityPrior
    knowledge_graph: TargetKnowledgeGraph
    # Canonical NCBI identity (gene ID, RefSeq protein accession, organism).
    # None when the target could not be resolved - see literature/ncbi.py.
    ncbi: dict | None = None


@dataclass
class ExperimentInterpretation:
    experiment_id: str
    target: Target
    prior: ExpectedAffinityPrior
    verdicts: list[Verdict]
    flagged_count: int


class TargetIntelligenceEngine:
    def __init__(self, mock: bool = True, api_token: str | None = None, literature_mode: str = "snapshot"):
        self.client = AdaptyvClient(mock=mock, api_token=api_token)
        self.literature_mode = literature_mode  # "snapshot" (real, frozen) | "live" (real PubMed) | "demo" (fixtures)
        # Calibrator is fit once, at startup, from the golden set - not
        # per-request. This mirrors how you'd deploy a calibrated model:
        # fit offline, ship the fitted temperature, don't refit live.
        # The per-target claims from that fit are kept too, so the
        # calibration-report tool can be served from this one fit rather
        # than refitting the whole golden set on every call.
        # Fit against the corpus actually being served - see
        # fit_calibrator_on_golden_set on why the two must match.
        self.calibrator, self.golden_claims = fit_calibrator_on_golden_set(
            corpus="demo" if literature_mode == "demo" else "snapshot"
        )
        # The fit is only valid for the distribution it was fit on - see
        # CalibrationStatus. Every prior this engine builds is stamped with
        # which regime it came from, so a live-mode confidence can never be
        # mistaken downstream for a calibrated one.
        self.calibration_status = (
            CalibrationStatus.CALIBRATED
            if literature_mode in ("snapshot", "demo")
            else CalibrationStatus.UNCALIBRATED_LIVE
        )
        self._context_cache: dict[str, TargetContext] = {}

    def _abstracts_for(self, target: Target, uniprot_hint: str) -> list:
        """Return this target's abstracts in the one shape the extraction
        layer understands, whichever mode we're in. Downstream of here
        nothing knows or cares which source it got.

        Three modes, in descending order of realism:

          snapshot (default) - real PubMed records, frozen. Real PMIDs and
              real citations, reproducible run to run, no network needed.
          live               - queries PubMed now. Most current, but results
              drift between runs and confidence is uncalibrated.
          demo               - hand-written offline corpus, retained for
              deterministic unit tests. Not a source of reportable numbers.
        """
        if self.literature_mode == "snapshot":
            return list(load_snapshot())
        if self.literature_mode == "demo":
            return DEMO_CORPUS

        from .literature.corpus import DemoAbstract
        from .literature.pubmed import PubMedClient  # local import: optional path

        # PubMed is searched by gene symbol, not accession - see the note on
        # Target.gene_symbol. Falling back to the accession would return an
        # empty result set that looks indistinguishable from a genuinely
        # sparse target, so this fails loudly instead.
        gene_symbol = target.gene_symbol
        if not gene_symbol:
            raise ValueError(
                f"Target {target.target_id} has no gene_symbol, which live literature mode "
                "needs to query PubMed. Set it on the target, or run in demo mode."
            )

        # Catalog names carry the common alias the papers actually use
        # ("HER2 / ERBB2" -> HER2), and clinical literature overwhelmingly
        # says HER2 while the approved symbol is ERBB2. Searching both
        # costs nothing and recovers papers the symbol alone misses.
        aliases = [part.strip() for part in target.name.replace("/", " ").split() if part.strip()]

        pubmed = PubMedClient()
        try:
            pmids = pubmed.search_target_binder_literature(gene_symbol, aliases=aliases)
            abstracts = pubmed.fetch_abstracts(pmids)
        finally:
            pubmed.close()

        # Adapt PubMedAbstract -> the (pmid_placeholder, target_hint, text)
        # shape build_target_claim expects. `target_hint` is set to the
        # uniprot hint so the claim keys identically in both modes.
        return [DemoAbstract(a.pmid, uniprot_hint, a.text) for a in abstracts]

    def get_target_context(self, target_id: str) -> TargetContext:
        if target_id in self._context_cache:
            return self._context_cache[target_id]

        target = self.client.get_target(target_id)
        uniprot_hint = target.uniprot_hint or target_id
        abstracts = self._abstracts_for(target, uniprot_hint)
        claim = build_target_claim(uniprot_hint, abstracts)
        calibrated_confidence = self.calibrator.calibrate(claim.raw_confidence)
        prior = build_prior(claim, calibrated_confidence, self.calibration_status)
        kg = build_knowledge_graph(claim, calibrated_confidence)

        ctx = TargetContext(
            target=target, claim=claim, prior=prior, knowledge_graph=kg,
            ncbi=load_identities().get(uniprot_hint),
        )
        self._context_cache[target_id] = ctx
        return ctx

    def interpret_experiment(self, experiment_id: str) -> ExperimentInterpretation:
        experiment = self.client.get_experiment(experiment_id)
        target_id = experiment.experiment_spec.target_id
        if not target_id:
            raise ValueError(
                f"Experiment {experiment_id} has no target_id (likely a "
                "thermostability/fluorescence/expression assay, which isn't "
                "target-binding and is out of scope for literature grounding)."
            )

        ctx = self.get_target_context(target_id)
        results: list[ResultRecord] = self.client.get_results(experiment_id)

        verdicts = [interpret_result(r, ctx.prior) for r in results]
        flagged = sum(1 for v in verdicts if v.flag_for_review)

        return ExperimentInterpretation(
            experiment_id=experiment_id,
            target=ctx.target,
            prior=ctx.prior,
            verdicts=verdicts,
            flagged_count=flagged,
        )

    def portfolio_coverage_report(self) -> list[CoverageNote]:
        """Portfolio-level gap analysis: which targets have literature-
        documented epitope diversity that a single-assay-type campaign
        might not confirm coverage of. See interpretation/coverage.py for
        the honesty scoping on what this can and can't claim."""
        notes: list[CoverageNote] = []
        for target in self.client.list_targets():
            ctx = self.get_target_context(target.target_id)
            note = epitope_diversity_note(target.target_id, ctx.target.name, ctx.prior)
            if note is not None:
                notes.append(note)
        return notes

    def calibration_report(self, n_bins: int = 5) -> dict:
        """How trustworthy the confidence scores themselves are, computed
        from the same single golden-set fit the engine runs on - so the
        number reported here is provably the number in use, not a second
        fit that could drift away from it.

        Deliberately reports `n_examples` alongside the ECE: an ECE quoted
        without its sample size is not an honest number at this scale.
        """
        from .literature.calibration import expected_calibration_error
        from .literature.golden_calibration import golden_set_labels

        labels_by_target = golden_set_labels()
        hints = list(self.golden_claims.keys())
        calibrated = [self.calibrator.calibrate(self.golden_claims[h].raw_confidence) for h in hints]
        labels = [labels_by_target[h] for h in hints]
        ece, bin_report = expected_calibration_error(calibrated, labels, n_bins=n_bins)

        return {
            "temperature": self.calibrator.temperature,
            "ece": ece,
            "n_golden_examples": len(labels),
            "bin_report": bin_report,
        }

    def close(self) -> None:
        self.client.close()
