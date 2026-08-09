---
name: literature-grounding
description: >
  Literature-grounded plausibility checks for binding and affinity results.
  Use when: (1) Interpreting a binding/affinity result after it comes back
  from an experiment, (2) Deciding whether a surprising KD needs orthogonal
  confirmation before it reaches a customer report, (3) Assessing how much
  literature precedent exists for a target before trusting a numeric prior,
  (4) Checking whether independent sources actually agree with each other,
  not just whether sources exist, (5) Building a portfolio-level view of
  which targets are well-characterized vs. novel, and which known epitope
  classes a campaign hasn't confirmed coverage of. Distinct from protein-qc
  (pre-experiment computational metrics) and binding-characterization
  (assay-physics troubleshooting) - this skill answers a third question: is
  this number scientifically expected, novel, or implausible given what's
  published about this target, and do the sources even agree with each
  other?
license: MIT
category: evaluation
tags: [literature, calibration, affinity, provenance, triage, evidence-level, coverage]
---

# Literature Grounding for Binding Results

## When you need this vs. when you don't

| Situation | Use this skill | Use instead |
|---|---|---|
| KD came back, want to know if it's expected for this target | Yes | - |
| Deciding if a result is trustworthy given assay physics (mass transport, NSB) | - | binding-characterization |
| Deciding if a design is worth synthesizing before it's tested | - | protein-qc |
| Ranking many designs by structural confidence | - | ipsae |
| No prior literature exists for the target at all | Yes (flags as novel_candidate, not an error) | - |
| Two papers report different affinities for the same target | Yes (flags as literature_conflict, doesn't silently average them) | - |
| Planning a campaign against a target with multiple known epitopes | Yes (`get_portfolio_coverage_gaps`) | - |

## How the check works (3-tier extraction ensemble)

1. **Pattern tier** - binder + epitope + affinity co-occurring in the same
   source (highest precision, narrowest recall).
2. **Proximity tier** - any binder mention + any affinity value in the same
   source, regardless of named epitope (wider recall, weaker precision;
   generic mentions like "a nanobody" score lower than named ones).
3. **Lexical-entailment tier** - scores whether a source's language supports
   "this target has an established, validated epitope" vs. "this target's
   literature is sparse."

The three tiers vote into a raw confidence score, which is then **temperature-
scaled** against a labeled golden set before it's shown to you - a raw
ensemble vote is not a calibrated probability, and this skill never presents
one as if it were.

## Reading a verdict

| Verdict | Meaning | Action |
|---|---|---|
| `consistent_with_literature` | KD falls within the range of validated binders for this target/epitope, or close enough that flagging it would be noise (within 5x of a bound - the rationale says which) | No flag - expected result |
| `novel_candidate` | Binding detected, but little/no literature precedent for this target | Soft flag - prioritize for epitope mapping, not an alarm |
| `outside_known_range_flag_artifact` | KD is far tighter than any literature precedent for a well-studied target | Hard flag - recommend orthogonal confirmation before reporting |
| `literature_conflict` | Independent sources disagree with each other (not just "sparse") | Flag - identify which binding format/context applies before concluding anything |
| `weaker_than_typical` | Binding detected but well below the range of established binders | No flag - lower priority, not an artifact concern |
| `qualitative_literature_only` | Binders/epitopes are known, but no numeric range could be extracted | No flag - insufficient data to compare quantitatively |
| `no_binding` | No meaningful signal | No flag - expected outcome for most designs in a screen |

## Evidence level: a second axis, separate from confidence

Confidence answers "how much literature is there." It does not answer "do
the sources that exist actually agree with each other" - a target can have
high confidence (two solid abstracts found) and still have those two
abstracts describe genuinely different affinity ranges. Blending them into
one min/max, as a naive aggregator would, manufactures a false consensus.
This skill tracks both axes separately:

| Evidence level | Meaning |
|---|---|
| `verified` | 2+ independent quantitative sources, and their ranges agree (within a tolerance band that absorbs ordinary between-paper variation) |
| `claimed` | Exactly 1 quantitative source - plausible, not yet corroborated |
| `conflicting` | 2+ quantitative sources, and their ranges genuinely don't overlap |
| `missing` | No quantitative source found at all |

A `conflicting` result most often reflects a real difference in
measurement context (e.g. bivalent IgG avidity vs. monovalent nanobody
affinity for the same epitope) rather than an error in either source - the
skill surfaces the disagreement and both citations rather than picking a
winner or averaging them.

## Portfolio coverage gaps

`get_portfolio_coverage_gaps` flags targets where the literature documents
multiple, independently-named epitope classes (e.g. domain II and domain IV
for HER2). This matters because a standard affinity/screening assay
confirms *that* something binds, not *which* epitope it engages - so a
campaign that looks complete against a target may only have explored one
of several known binding sites.

**Scope, stated honestly**: this does not claim to know which epitope any
specific sequence in your results actually hit - that requires
epitope-binning or a competition assay against a reference binder, which
this skill doesn't perform. It only tells you *when that check is worth
running* - specifically, when the literature shows enough epitope diversity
that "one screen against this target" and "full coverage of this target"
aren't the same claim.

**And it currently finds nothing on the shipped snapshot.** The tool is
gated on extracted epitope spans, and epitope recall on real abstracts is
the weakest part of the extractor (see Known limitations) - across the five
snapshot targets it finds one epitope total, below the two-class threshold.
The logic is exercised by unit tests against text that does name multiple
classes. Read it as a capability waiting on better epitope extraction, not
as a result.

**Read `analysis_status` before you report anything.** The tool never
returns a bare empty list, because "no gaps found" and "the check could not
run" are opposite conclusions and an empty list is indistinguishable
between them:

| `analysis_status` | What to tell the user |
|---|---|
| `gaps_found` | The listed targets have multi-epitope literature; recommend epitope-binning. |
| `insufficient_epitope_data` | **The check could not run.** Do not say coverage is fine - say epitope extraction found too little to assess it, and quote `n_targets_with_any_epitope`. |

## Why did a plausible-looking KD get flagged?

| Symptom | Likely cause | What to check |
|---|---|---|
| Sub-picomolar KD on a well-studied target | Literature caveat: affinities below the stated ceiling are noted as uncommon and usually reflect avidity/multivalency, not true monovalent affinity | Re-run in a strictly monovalent format (e.g. Fab, not IgG) |
| Low confidence despite binding data existing | Sparse literature, not bad data - few/no established binders reported for this target at all | Treat as a real novelty signal, not noise |
| High confidence but no numeric range shown | Binders/epitopes are named in the literature, but no extractable affinity value | Fall back to qualitative comparison only |

## Calibration quality

Report before trusting any confidence score:

| Metric | What it means |
|---|---|
| Expected Calibration Error (ECE) | How far predicted confidence deviates from actual reliability, binned |
| Fitted temperature | How much the raw ensemble vote had to be rescaled to be honest |
| Golden-set size | Sample size the calibration was fit on - report this, don't hide it |
| `calibration_status` | Whether the confidence is a probability at all, or only an ordering signal |

**Critical limitation**: this skill's golden set is illustrative-scale (14
labeled examples), not production-scale. The reference architecture (see
References) was validated at 5,000+ source scale in a related biomedical NLP
pipeline; this deployment is intentionally small so the calibration number
stays honest about what it was actually fit on. Current measured performance
on the default real-literature snapshot: **temperature = 3.99, ECE = 0.322,
n = 14** (0.224 on the easier hand-written demo corpus - real abstracts are
harder, and the number moves accordingly rather than being reported from the
flattering corpus). `get_calibration_report` always returns
`n_golden_examples` next to the error - an ECE quoted bare at this sample
size would mislead.

### Why the fit follows the corpus

The temperature is fit on **whichever corpus is being served**, because a
temperature fit on one corpus is only valid for scores drawn from it. Real
PubMed abstracts score systematically lower on the same ensemble than tidy
hand-written ones - the epitope pattern rarely fires on real phrasing, and
the tier-1 hit requires an epitope. Fitting on demo text and then serving
real text reported **HER2 at confidence 0.00** - a target with agreeing
quantitative sources. The number wasn't wrong so much as meaningless,
because it answered a question about a different distribution.

So `snapshot` and `demo` modes each get their own fit, and live mode - which
can't be fit ahead of time, since the text changes per query - declares
itself. Every prior carries a `calibration_status`:

| Value | Meaning |
|---|---|
| `calibrated` | `snapshot` or `demo` mode. The confidence matches the distribution the temperature was fit on. |
| `uncalibrated_live` | Live PubMed mode. Treat the confidence as an **ordering signal only**, not a probability. |

In live mode the caveat is appended to the verdict rationale itself, not
just carried in a field - a disclosure nobody reads is not a disclosure.
Making live confidence meaningful needs a golden set labeled on real
abstracts, which this deployment does not have.

## Composing with the Foundry MCP server

This skill is additive, not a replacement for Adaptyv's own Foundry MCP
tools. Typical composition:

1. Call the Foundry MCP server's experiment/result tools to pull a finished
   experiment's results.
2. Hand each result to this skill's `interpret_experiment_result` tool
   along with the target ID.
3. Read back a verdict per sequence, each with a rationale, evidence level,
   and citations.
4. Before starting a new campaign, call `get_portfolio_coverage_gaps` to
   check whether the intended target has enough documented epitope
   diversity to warrant planning for epitope-binning alongside the primary
   assay.

## Known limitations

- **Epitope recall on real abstracts is weak.** The epitope pattern matches
  `domain I-V` / `IgV domain`; published abstracts often phrase it
  differently. This is the main reason live-mode confidence runs low, and
  fixing it properly means a trained model rather than more regex.
- **The NER is a documented stand-in**, not a fine-tuned model: a
  rule/gazetteer extractor filling the same architectural slot.
  `extract_entities()` is the single interface a real model would replace.
- **Coverage analysis cannot identify epitopes** - see the scope note above.
- **The demo corpus is originally written**, labeled `DEMO####`, and is not
  quoted or scraped from any real abstract.

## References

- Reuses the retrieval -> NER -> 3-tier relation extraction -> temperature
  scaling -> provenance-tagged knowledge graph architecture from an existing
  biomedical NLP pipeline (MSc thesis, MCP-native, deployed with health
  checks; NER F1 0.884, relation-extraction F1 0.815, ECE 0.038 post-
  calibration at full corpus scale). Those figures belong to that pipeline
  and are **not** claimed for this skill, whose own measured numbers are in
  the calibration section above. No code, weights or data are shared
  between the two.
- Guo et al. 2017, "On Calibration of Modern Neural Networks" - temperature
  scaling methodology.
- NCBI E-utilities documentation (live literature retrieval mode).
