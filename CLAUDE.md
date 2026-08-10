# CLAUDE.md — working context for this repo

Read this before changing anything. It records the decisions that are easy
to undo by accident, and why they're there.

## What this is

`target-intel` answers one question: **is this binding result plausible for
this target, given what's already published?** It sits between
Adaptyv's `protein-qc` (pre-experiment design metrics) and
`binding-characterization` (post-experiment assay physics) — neither of
which reasons about the target's published binder/affinity landscape.

Ships as a Claude Code plugin + 7-tool MCP server + CLI + dashboard.

## Commands

```bash
pip install -e ".[dev]"
pytest                                       # 164 tests, offline, ~10s
target-intel interpret <experiment-id>       # the core output
target-intel score                           # exact-span entity F1 vs. gold
target-intel eval                            # calibration + regression guards
python scripts/build_literature_snapshot.py  # rebuild corpus (NEEDS NETWORK)
python -m target_intel.dashboard.generate_report && \
  python -m target_intel.dashboard.build_dashboard
docker compose up -d && curl localhost:8002/health
```

## Architecture

```
Foundry result ─┐
                ├─> compare ─> cited verdict ─> MCP · CLI · dashboard · Neo4j
target literature ─> NER ─> 3-tier ensemble ─> calibrate ─┘
```

`TargetIntelligenceEngine` (`engine.py`) is the **only** place a verdict is
computed. MCP, CLI, dashboard and the Neo4j loader all call into it. Keep it
that way — three copies of "what does this result mean" will drift.

| Module | Role |
|---|---|
| `sdk/` | Typed Foundry client. `mock=True/False` swaps transport, same models out |
| `literature/eutils.py` | Shared NCBI transport: throttle, retry, identification |
| `literature/ncbi.py` | Gene symbol → canonical Gene ID + RefSeq protein |
| `literature/pubmed.py` | Retrieval + XML parsing |
| `literature/ner.py` | Entity extraction (regex + gazetteer) |
| `literature/relation_extraction.py` | 3-tier ensemble → `TargetLiteratureClaim` |
| `literature/scorer.py` + `gold.py` | Exact-span F1 vs. hand-labeled gold |
| `interpretation/verdict.py` | The rule cascade producing 7 verdicts |
| `mcp_server/` | 7 tools + `/health` |

## Invariants — don't break these

**1. Every number ships with its sample size.** F1 with span count, ECE with
n. `get_calibration_report` returns `n_golden_examples`. This is the
project's whole credibility posture; a bare metric is a regression.

**2. The calibrator must be fit on the corpus being served.**
`fit_calibrator_on_golden_set(corpus=...)` follows `literature_mode`.
Fitting on demo text and serving real text reported HER2 at confidence 0.00
— a target with agreeing quantitative sources. If you add a corpus, add a
fit for it.

**3. `demo` mode is test fixtures only.** `corpus.py` is hand-written. It
exists so unit tests are deterministic. **Never quote a number derived from
it as a result.** Default mode is `snapshot` (real PubMed).

**4. Don't commit abstract text.** The repo ships derived data — PMIDs,
citation metadata, extracted spans, ≤200-char excerpts. Full text caches to
gitignored `.cache/`. Redistributing publisher abstracts is the line.

**5. The MCP server must work on mcp 1.x *and* 2.x.** `server.py` imports
`MCPServer` (2.x) falling back to `FastMCP` (1.x). Tests drive it over a
real stdio subprocess, because tool functions can pass while the server
can't start — that was a real bug here.

**6. No test may touch the network.** The snapshot ships in the repo;
`tests/data/efetch_sample.xml` is a saved real response. A test that hits
NCBI is flaky by construction.

**7. Verdict cascade order is load-bearing.** In `verdict.py`, the
"outside the whole envelope" check runs **before** the conflict branch. A
value below every published source is implausible whether or not sources
agree, and putting conflict first made it swallow every other verdict on
real data.

**8. `nM` is matched case-sensitively.** `nm` is nanometres. The first
apparent "affinity" in live PubMed output was a particle diameter. Bare `M`
and `mM` are excluded — at those concentrations it's a buffer.

**9. Affinity values must be *qualified*.** A number with molar units isn't
an affinity. `is_affinity_qualified()` requires a dissociation-constant cue
nearby and rejects IC50/EC50/concentration context. Without it HER2's prior
spanned 1e-13 to 1.5e-7 M.

**10. Priors use the robust core, not the envelope.** Real affinities span
orders of magnitude legitimately (engineered sub-pM constructs → 151 nM
clones). `low_m`/`high_m` are the log-space IQR; `envelope_*` is reported
for transparency and never used for a verdict.

**11. A rationale must not state a number is inside an interval it prints
next to it.** `consistent_with_literature` covers two cases — inside the
core, and outside it but within the 5× tolerance band. They read
differently in `verdict.py` because "falls within the range (3.16e-13 M–
1.34e-09 M)" next to a 1.70e-09 M result is a contradiction on the page,
and that's the first thing a scientist checks. Pinned by
`test_consistent_rationale_never_claims_a_kd_is_inside_a_range_it_is_outside`.

**12. An empty MCP result must say why it's empty.** An agent handed `[]`
has two readings available — "nothing found, all clear" and "the check
couldn't run" — and they lead to opposite advice to a scientist.
`get_portfolio_coverage_gaps` returns `{gaps, analysis_status, note, ...}`,
never a bare list. Same rule for anything new: type the empty case. Pinned
by `test_empty_coverage_result_says_why_rather_than_reading_as_all_clear`
and its over-the-wire twin.

**13. Graph edges carry the core range and their own attribution.** A
`BINDS` edge outlives the run that wrote it, so `kd_low_m`/`kd_high_m` are
the IQR core (never the envelope, which travels as `kd_envelope_*`), and
every edge carries `attribution: "target_level"` — extraction links an
affinity to its *abstract*, never to the binder named in it. `to_cypher()`
leads with a `//` header saying `BINDS` means "co-mentioned in this
target's literature", because a HER2 query genuinely returns cetuximab.

## Known-unverified paths

State these plainly; don't quietly imply otherwise.

- **Foundry live API** — never run against a live token. The *models* are
  no longer unverified though: `tests/data/foundry_experiment_response.json`
  is a real captured response, and it did **not** parse until `id`/`code`
  aliases and the nested-`costs` normaliser were added. Transport, auth and
  pagination remain untested against the real service.
- **Neo4j live load** — logic unit-tested with a fake session, `--dry-run`
  emits exact Cypher, but nothing has been written to a real database
  (Docker wouldn't start in the dev environment).
- **Portfolio coverage finds nothing on the shipped snapshot.** Epitope
  recall on real abstracts finds one epitope across all five targets, below
  the two-class threshold `epitope_diversity_note` needs. The logic is
  unit-tested against text that names multiple classes. Don't demo it as a
  *finding*; don't "fix" it by lowering the threshold. The tool returns
  `analysis_status: insufficient_epitope_data` with a note, never a bare
  `[]` — see invariant 12.

## Measured numbers (regenerate, don't trust this file)

| Metric | Value | Command |
|---|---|---|
| Tests | 164 | `pytest` |
| Entity F1, held-out | 0.919 (n=20 spans) | `target-intel score` |
| Entity F1, in-domain | 1.000 (n=35) — **a ceiling, not a result** | `target-intel score` |
| `BINDER_NAMED` recall | 0.700 — the gazetteer's real limit | `target-intel score` |
| ECE | 0.322 (n=14) | `target-intel eval` |
| Real papers | 77 across 5 targets | snapshot |

The in-domain 1.000 measures consistency, not generalisation — the gazetteer
was written against that text. Always report the held-out number.

## Style

Match the surrounding code. Comments explain *why*, especially where a
simpler-looking approach was tried and failed — several of the invariants
above are documented at their call site for exactly that reason. Tests are
named for the behaviour they pin, not the function they call.
