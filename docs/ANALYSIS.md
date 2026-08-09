# Adaptyv Bio take-home — what was built, and why it fits

**Project:** `literature-grounding` / `target-intel`
**Repo:** [`AMVamsi/adaptyv-target-intel`](https://github.com/AMVamsi/adaptyv-target-intel) · [README](../README.md)
**Status:** 159 tests passing on both mcp 1.x and 2.x · **runs on real PubMed literature by default** (77 papers, real PMIDs) · extraction quality measured against a hand-labeled gold set · health-checked container + Neo4j loader.

---

## 1. The one-sentence version

Adaptyv's lab returns a binding result; existing tooling can tell you whether
the *design* was worth making and whether the *curve* is trustworthy, but
nothing tells you whether the **number is plausible for that target given
what's already published**. This builds that check, packaged as an
installable Claude Code skill and a 7-tool MCP server so it runs on every
result instead of only the ones someone happens to scrutinise.

---

## 2. Why this problem, and not another API wrapper

The take-home said "build something you think would actually be useful."
The temptation is to wrap the Foundry API in an SDK and an MCP server. That
was rejected for two checkable reasons:

1. **Adaptyv already shipped it.** Public REST API, official `adaptyv-sdk`,
   a live MCP server at `mcp.adaptyvbio.com` already wired into Benchling AI
   and Tamarind. Rebuilding mature infrastructure demonstrates nothing.
2. **That shape competes on polish, not judgment.** A typed SDK and an MCP
   server around a mature public API is a known quantity: the interesting
   decisions were made when the API was designed. Rebuilding it well proves
   care, but proves nothing about whether I can find a gap worth filling.

So the bet was: **compete on domain judgment layered on top of plumbing that
is already solved.**

### The gap is verified, not guessed

The two nearest skills in `adaptyvbio/protein-design-skills` were read in
full before committing:

| Skill | Question it answers | When |
|---|---|---|
| `protein-qc` | Is this design worth synthesizing? (pLDDT, ipTM, PAE, liabilities) | Before the experiment — computational |
| `binding-characterization` | Is this curve trustworthy? (mass transport, NSB, regeneration, hook effect) | After the experiment — assay physics |
| **`literature-grounding`** | **Is this number plausible for this target?** | **After the experiment — domain knowledge** |

Neither reasons about a target's published binder / epitope / affinity
landscape. A result can pass both existing checks and still be biologically
implausible. The HER2 case in the demo is exactly this: a 50 fM result with
R² 0.998 and clean kinetics, where the only published affinity anywhere near
it comes from a *multivalent* construct measuring avidity rather than 1:1
binding — a distinction no numeric QC check can make.

This maps directly onto a named JD bullet: *"AI systems that troubleshoot
production results and accelerate assay development."*

---

## 3. What was actually built

```
Foundry result ─┐
                ├─> compare ─> cited verdict ─> MCP / CLI / dashboard
target literature ─> NER ─> 3-tier ensemble ─> calibrate ─┘
```

**~3,600 lines of source, ~1,500 lines of tests**, across:

| Component | What it is |
|---|---|
| `sdk/` | Typed Foundry client; one interface, `MockTransport` (fixtures) or `LiveTransport` (real API), identical shapes either way |
| `literature/` | PubMed retrieval, entity extraction, 3-tier relation-extraction ensemble, temperature calibration, provenance-tagged KG with Cypher export |
| `interpretation/` | Literature claim → expected-affinity prior → cited verdict; plus portfolio epitope-coverage analysis |
| `mcp_server/` | 7 MCP tools over stdio |
| `cli.py` | 7 subcommands, human-readable by default, `--json` for CI |
| `dashboard/` | Self-contained HTML portfolio view, zero deps, zero server |
| `evals.py` | Calibration report + deterministic regression guards |
| `.claude-plugin/` | `plugin.json` + `marketplace.json` + `.mcp.json` — installs like Adaptyv's own skills repo |

### Seven verdicts, not a binary flag

`consistent_with_literature` · `outside_known_range_flag_artifact` ·
`literature_conflict` · `novel_candidate` · `weaker_than_typical` ·
`qualitative_literature_only` · `no_binding`

The distinctions carry the value. A sparse-literature target returns
`novel_candidate` ("nothing to compare against — prioritise for epitope
mapping"), which is a *different claim* from `consistent_with_literature`,
and the system never conflates them.

### Two axes, because one number hides the interesting case

Confidence answers *how much* literature exists. It does not answer whether
the sources **agree**. On the real snapshot HER2 has three quantitative
sources spanning avidity-driven sub-picomolar to 151 nM — because they
measured different constructs. A naive aggregator takes min and max and
reports one five-order-of-magnitude "consensus" range that no source
claims.

Instead: a separate `evidence_level` axis (`verified` / `claimed` /
`conflicting` / `missing`), and a `literature_conflict` verdict that cites
both sources and **declines to conclude** until someone decides which
binding format the comparison should use.

---

## 4. What changed in this refinement pass

The project existed in draft. Refining it surfaced real defects — worth
listing because finding them is the substance of the work:

| Found | Severity | Resolution |
|---|---|---|
| **MCP server could not be imported.** `mcp.server.mcpserver` didn't exist in the installed SDK. The handoff doc claimed "53/53 tests passing"; the real count was 52/53, and the failure was the MCP server — the single component most central to this role. | Critical | Server now imports whichever SDK generation is present; `tests/test_mcp_protocol.py` drives it over a **real stdio subprocess**. Suite verified on mcp **1.29.0 and 2.0.0**; CI resolves `mcp<2.0` and `mcp>=2.0` on every run, so the pair tested tracks whatever is current rather than the pair that happened to be installed the day this was written. |
| **Live PubMed mode extracted nothing.** Marked "fully implemented, just not exercised." Network was in fact available; it had simply never been run. Running it returned 20 real abstracts and **zero** entities. | High | Three underlying bugs fixed (below). Live mode now returns real binders, real PMIDs, real ranges. |
| **PMID↔abstract pairing by list position.** `retmode=text` + `zip(pmids, chunks)` silently mis-attributes every citation after the first record with no abstract — common for reviews and editorials. A wrong PMID on a verdict is worse than no verdict. | High | Switched to `retmode=xml`; each abstract now reads its PMID from the record it belongs to. Records without abstracts are dropped, not shifted. |
| **NER only understood spelled-out units.** The demo corpus says "5 nanomolar"; real abstracts say "KD = 2.3 nM". | High | Symbol notation added — **case-sensitively**, because `nm` is nanometres and `nM` is nanomolar. The first apparent "affinity" hit in live output was exactly this false positive. Bare `M`/`mM` excluded: at those concentrations it's a buffer, not a binding constant. |
| **PubMed query far too loose.** gene AND (binding OR affinity OR antibody) returned 18 ERBB2 abstracts with zero usable affinities — "antibody" matches every ADC and clinical-outcome paper. | Medium | Measurement terms required as their own AND-ed clause: **0/18 → 4/20** abstracts with usable affinities, 19/20 mentioning a binder. Recall of *papers* drops; recall of *evidence* rises. |
| **Calibrator applied outside its fit distribution.** Temperature is fit on the demo corpus; live abstracts score systematically lower, so live confidences were being presented as probabilities they weren't. | High | Every prior stamped `calibrated` / `uncalibrated_live`; the caveat is appended to the **rationale text**, not just a field. Pinned by tests, including one asserting the caveat never changes a verdict label. |
| **`target-intel eval` broke after `pip install`.** It loaded the eval script by filesystem path, which isn't packaged. | Medium | Logic moved into the package; the script is now a thin wrapper. Verified by installing into a clean venv and running the console script from an unrelated directory. |
| **Tests only passed by inheriting the parent repo's pytest config.** No `pip install`, no `PYTHONPATH` → collection error. | Medium | Self-contained `[tool.pytest.ini_options]` with `pythonpath = ["src"]`. Plain `pytest` now works from a clean clone. |
| **No README.** The project had no front door at all. | Medium | Written, leading with the concrete EGFR failure case. |
| **No plugin scaffolding.** | Medium | `.claude-plugin/plugin.json`, `marketplace.json`, `.mcp.json`, validated against the official schema. |

**Test count: 52/53 → 159/159**, with the new tests concentrated exactly
where the bugs were: `test_mcp_protocol.py` (7), `test_pubmed_live_path.py`
(10), `test_ner_real_notation.py` (9), `test_calibration_status.py` (7),
`test_scorer.py` (14), `test_neo4j_loader.py` (9), `test_health.py` (7).

### Switching to real literature — the most valuable pass

The demo corpus was replaced with a frozen snapshot of **real PubMed
records** (77 papers, 5 targets). Every claim about what this does is now
checkable against papers a reviewer can open. It also broke four things
immediately, and each break is worth more than the demo it replaced:

| Real-data failure | What it revealed | Fix |
|---|---|---|
| HER2's prior spanned **1e-13 – 1.5e-7 M** | The extractor counted every number with molar units — IC50s, working concentrations, doses | **Qualification**: a value counts only when its clause presents it as a dissociation constant; rejected when an IC50/EC50/concentration cue sits closer |
| Even correct affinities span orders of magnitude | Real HER2 binders genuinely run sub-pM (avidity constructs) → 151 nM. No extraction fixes this | **Robust core**: log-space IQR, since affinities are log-normal. Envelope still shown, never used for verdicts |
| **Every** target read `conflicting` | Pairwise agreement flags if *any* two sources differ; across enough real papers, some pair always does | **Hybrid**: pairwise below 4 sources, consensus-vs-core above |
| **Every** target read `sparse` — HER2 included, with 19 papers | Density was read off a confidence whose temperature was fit on a different corpus | Density counts quantitative sources; calibrator refit on the corpus actually served |

A fifth, found by reading the extracted output: `KD = 5.71 ± 3.89 nM` was
recording **3.89** — the uncertainty — as an independent affinity. Now
rejected.

**Calibration honesty.** Refitting on real data moved ECE **0.224 → 0.322
(n=14)**. That degradation is reported, not hidden: real literature is
harder than prose written to be parsed.

**One design decision worth defending.** A 50 fM HER2 result is *not*
flagged as an artifact — because the tightest published HER2 affinity is a
multivalent construct where sub-pM reflects avidity, so the honest answer is
"your comparators disagree and the tightest isn't measuring the same thing",
not a confident anomaly call. Conflict is reserved for results in that
contested zone; ordinary binders are still answered cleanly.

### Direct ports from the thesis codebase

Four components were carried over rather than reinvented, each closing a
specific credibility or deployability gap:

| Ported from thesis | Gap it closed |
|---|---|
| **Exact-span entity scorer** (`f1_scorer.py`) | Extraction quality was *unmeasured*. "Documented stand-in" was an unfalsifiable claim; now it's **held-out micro-F1 0.919 on 20 labeled spans** (0.972 across all 55), with `BINDER_NAMED` recall 0.700 quantifying the gazetteer's exact limitation. |
| **`/health` + Compose healthchecks** | The server ran but wasn't *deployable*. Now: containerised, streamable-HTTP transport, `/health` returning 503 when degraded. |
| **Neo4j loader with provenance + QC gate** | The KG was a Cypher string that went nowhere. Now idempotent `MERGE`s, allowlisted labels, and a QC gate that fails if any `BINDS` edge lacks PMID provenance. |
| **Temperature scaling + binned ECE** | Already present in draft; the thesis's *reporting discipline* (never quote a calibration figure without n) was what actually transferred. |

Building the gold set also surfaced a genuine extractor bug: overlapping
gazetteer terms (`engineered small binder` / `binder scaffolds`,
`single-domain binder` / `binders`) double-counted a single mention,
inflating tier-2 hit counts and duplicating `known_binders` entries. Fixed
with longest-match-wins overlap suppression — found only because the
extractor was finally being scored.

---

## 5. How this aligns with my profile

Not thematic resemblance — the same pipeline shape, rebuilt for a different
domain, with the numbers from the original defensible under questioning.

| MSc thesis (ZHAW, validated at scale) | This project |
|---|---|
| PubMed retrieval, E-utilities + MeSH expansion, 5,165 articles | Target-literature retrieval, live E-utilities, verified end-to-end |
| PubMedBERT NER fine-tune, **micro-F1 0.884** | Rule/gazetteer extractor in the same architectural slot, documented as a stand-in |
| 3-tier RE ensemble (co-occurrence → fine-tuned classifier → NLI zero-shot), **micro-F1 0.815** | 3-tier ensemble (pattern → proximity → lexical-entailment) |
| Temperature scaling, **ECE 0.2175 → 0.038** | Temperature scaling, ECE 0.322 at n=14 — reported *with* its sample size |
| Neo4j KG, **33,246 provenance-tagged triples** | Provenance-tagged KG, Cypher-exportable |
| MCP-native, Docker Compose, health-checked | 7-tool MCP server, installable plugin |
| "Known Limitations" section listing every caveat | Same discipline, same section |

**The last row is the real transfer.** The thesis README documents that
`tier1_bc5cdr` scores MICROBE = 0.000, that GENE/PHENOTYPE have no gold
evaluation, and that the GPU environment isn't reproducible from
`environment.yml`. That habit is why this project ships an
`uncalibrated_live` flag instead of a confident-looking number.

### On IP and attribution

Four modules are ported from the thesis codebase (table in §4), each with
in-file provenance comments naming the source file. **No model weights,
training datasets or corpora were copied** — the thesis NER model is
domain-specific to microbiome entities (MICROBE / DISEASE / METABOLITE) and
would not transfer to binder/epitope/affinity extraction even if shipped.
What transfers is engineering: a scorer, a health payload, a graph loader,
and a reporting discipline.

The thesis's scale figures are cited as belonging to that pipeline and are
explicitly *not* claimed for this prototype, whose own measured numbers
(held-out F1 0.919 on n=20 spans, ECE 0.322 on n=14) are reported separately
with their sample sizes. The demo corpus is originally written and labeled
`DEMO####`; the demo targets match the style of Adaptyv's own published
example (`ABS-001-042`, anti-HER2 VHH screen).

**Where the boundary sits.** The thesis was carried out with an industry
partner, and the reuse here was checked against that before publishing: what
transfers is engineering this project re-implements for a different
domain — an exact-span scorer, a health payload, a graph loader with a
provenance gate, and temperature scaling — with in-file comments
naming the original file. No model weights, no training data, no corpora, and
no partner data of any kind.

Being able to state that boundary precisely is itself relevant: this role
touches customer experiment data, and knowing exactly what may and may not
travel between projects is part of the job.

---

## 6. Why it's useful to the team's existing system

Mapped against the JD's own bullets:

| JD bullet | This project |
|---|---|
| "Wrapping our internal APIs into clean, installable SDKs and MCP servers so agents and teammates can plug into them in minutes" | One typed client, mock or live, identical shapes. 7 MCP tools. `pip install -e .` → working CLI. Plugin install wires up the MCP server automatically. |
| "AI systems that troubleshoot production results" | The entire product. |
| "Setting up evals, observability and monitoring… catch regressions automatically" | Three layers: `target-intel score` (measured extraction F1 vs. gold), `target-intel eval` (calibration + deterministic guards that hold independent of calibration drift), and `/health` returning 503 when degraded. All offline, no LLM calls, no cost — CI-able as-is. |
| "Pulling together experiment data to surface insights the team would otherwise miss" | Portfolio dashboard + epitope-coverage gap analysis across the whole target set. |
| "Taking a powerful-but-buried capability and making it the new default" | Packaged in Adaptyv's exact house style so it reads as native, not bolted on. |

**Where it plugs in.** It is deliberately *additive*: it sits next to
Adaptyv's Foundry MCP server rather than replacing it. Pull results with
their tools, hand each to `interpret_experiment_result`, get a cited verdict
back. The natural integration point is the customer-report path — the
`outside_known_range_flag_artifact` verdict exists precisely to catch a
result before it reaches a customer.

**Honest read on immediate value.** The verdict logic, MCP surface, eval
harness and packaging are production-shaped. The *extraction quality* is
not: the rule-based NER and the demo corpus are illustrative. Pointed at
Adaptyv's real internal data — their own historical results as the prior
rather than PubMed — the same skeleton becomes considerably more useful,
and needs no architectural change to get there. That's the honest sequencing.

---

## 7. Known limitations

Stated here so they're in one place, and because a submission that hides
these is worth less than one that doesn't:

- **Epitope recall on live text is weak.** The pattern matches `domain I–V` /
  `IgV domain`; real abstracts phrase it differently. This is why
  well-studied targets score low in live mode — the tier-1 hit needs an
  epitope. Fixing it properly means a trained model, not more regex.
- **Live-mode confidence is not calibrated**, and says so. A real fix needs
  a golden set labeled on real abstracts.
- **The Foundry live path is unexercised** — no API token. Written against
  the documented public API, transport swappable, but not verified. Unlike
  the PubMed path, this one could not be tested.
- **The Neo4j load path is unverified against a live database** — Docker
  could not be started here. Logic is unit-tested against a fake session and
  `--dry-run` emits the exact Cypher, but nobody has watched it write.
- **Literature is real; experiment results are not.** There is no Foundry
  token, so KD values in `sdk/fixtures/` are authored test cases — one per
  verdict path, chosen to probe the *real* priors. Labelled as such.
- **The calibration golden set is 14 examples; the span gold set is 22
  sentences / 55 spans.** Both illustrative, not production scale, and
  quoted that way everywhere.
- **The NER is a rule/gazetteer stand-in**, not a fine-tuned model — and its
  ceiling is now measured rather than assumed (`BINDER_NAMED` recall 0.700).
- **Coverage analysis cannot identify epitopes** — it flags when
  epitope-binning is worth running, nothing more.
- **`MockTransport` is read-only**, no pagination, no s-expression filtering.

### What I'd do next, in order

1. A golden set labeled on real abstracts → genuine live-mode calibration.
2. Epitope extraction — the actual bottleneck on live text.
3. Swap the prior source from PubMed to Adaptyv's own historical results.
4. Exercise the Foundry live path against a real account.

---

## 8. Facts a reviewer can check in under two minutes

```bash
git clone https://github.com/AMVamsi/adaptyv-target-intel && cd adaptyv-target-intel
pip install -e ".[dev]" && pytest          # 159 passed, offline, ~10s
ruff check src tests scripts evals                             # clean

target-intel interpret 019d4a2b-3c5e-7890-a001-000000000001    # HER2: 1 of 4 flagged, real PMIDs
target-intel interpret 019d4a2b-3c5e-7890-a003-000000000003    # GPR35: novel_candidate, no prior invented
target-intel coverage                                          # an empty result that says why it's empty

target-intel score                                             # held-out F1 0.919 (n=20 spans); 0.972 overall (n=55)
target-intel eval                                              # ECE 0.322 (n=14), guards pass

target-intel context comp-her2-human --live-literature         # real PubMed — needs network
docker compose up -d && curl localhost:8002/health             # deployed + health-checked
```
