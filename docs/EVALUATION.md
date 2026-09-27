# Evaluation

GRC Audit Swarm has two layers that can be wrong in different ways, and each is evaluated separately.

| Layer | What can go wrong | How it is evaluated | Where |
|---|---|---|---|
| Evidence | The AWS read reports the wrong configuration, leaks an account ID, or is not stored in the vault | Deterministic: planted misconfigurations in moto, exact expected output | `tests/eval/` (runs in CI) |
| LLM | The agents draw the wrong audit conclusion from correct evidence: a pass where the evidence shows an exception, a conclusion with no evidence behind it, a single configuration read treated as operating effectiveness, a severity outside a reasonable range | This harness: scenarios with a versioned answer key, run through the real pipeline with your LLM provider | `evals/` (run by the owner; never in CI) |

> **No published results yet. Run the harness with your provider.** The answer key is a draft that the owner has not reviewed yet (`reviewed_by: null`), and no real-model run has been recorded. Nothing in this repository reports a measured accuracy figure for the LLM layer.

## How the harness works

For each scenario and each run:

1. **Simulated account.** A fresh in-memory AWS account (moto) is seeded with the scenario's planted state: password policy, IAM users with or without MFA, S3 buckets with ACLs, policies and Block Public Access. The harness sets fake AWS credentials and removes `AWS_PROFILE`, so it cannot reach a real account. The evidence vault is a folder inside the run's output directory.
2. **Real pipeline.** The application's own `AuditFlow` runs Planning, Fieldwork and Reporting with the real crews, the configured LLM provider (`swarm.llm_factory`, including a separate `QA_LLM_MODEL` if set), the QA retry, the deterministic evidence check and the three gates. A synthetic reviewer approves each gate. If a QA rejection survives the automatic retry, the synthetic reviewer overrides it by default so the later phases can still be measured (`--on-qa-reject stop` ends the run instead). Every rejection and override is recorded in the results.
3. **Mapping.** RACM control IDs are written by the model, so findings are mapped to answer-key **control areas** (password policy, IAM MFA, S3 public access, root user, access review, provisioning, change management, logging, encryption, backup). The mapping is deterministic: weighted keyword hits in the finding's RACM control text, plus a bonus when the cited vault record comes from the area's evidence tool. Ties are resolved by catalogue order and flagged `ambiguous`. Findings that match no area, or match a tool area the scenario's key does not cover, are listed as **unmatched** in the report. They are never silently dropped. Each run's raw JSON keeps the full mapping record (text used, per-area score and keyword hits).
4. **Scoring** against the answer key (metrics below). Scoring is a pure function of the saved raw run, so every number can be recomputed from the JSON. A test checks this.
5. **QA seeding.** Separately, deliberately bad working papers are given to the Fieldwork QA reviewer to measure how many it rejects.

## Answer key

`evals/scenarios/answer_key.v1.yaml` is versioned (`version`) and carries its review status. It is a **draft written with AI assistance** from an experienced IT auditor's point of view, and the owner must review and sign it off by setting `reviewed_by` and `reviewed_on`. Until then every report starts with "ANSWER KEY NOT YET REVIEWED BY THE OWNER".

For each scenario and control area the key gives the expected result (`Exception`, `No exception` or `Not tested`), the acceptable ToD and ToE conclusions, the number of exceptions the evidence shows (where it applies), the deficiency classifications a reviewer would accept, and a rationale. Areas with no evidence tool have a default expectation of `Not tested` in every scenario. The key validates itself on load: a tool-less area cannot expect a tested outcome, and classifications must be on the scenario's scale. A test also checks that the planted evidence (the real tool output from the seeded account) agrees with each expected outcome.

### Scenarios

| ID | Scope | Planted state | Expected (per area) |
|---|---|---|---|
| s01 | SOX logical access (ICFR scale) | No password policy; 40 of 60 users without MFA | Password: Exception (ToD Ineffective). MFA: Exception, 40 exceptions. Access review: Not tested. Classification: Control or Significant Deficiency |
| s02 | ISO 27001 identity baseline | Strong policy; 12 users, all with MFA | Password, MFA: No exception, ToD Effective, ToE Not tested or Effective with a stated reliance |
| s03 | Password standard (length 14) | Length 6, no symbols or uppercase | Password: Exception (ToD Ineffective); Medium or High |
| s04 | MFA, payments account | 20 of 60 users without MFA | MFA: Exception, 20 exceptions; Medium or High |
| s05 | S3 exposure, customer exports | Bucket public through a `Principal: "*"` policy | S3: Exception, 1 exception; Medium or High |
| s06 | S3 exposure | Public-read ACL, but all four account-level Block Public Access flags on | S3: **No exception**. The ACL is ignored, and raising it is a false fail |
| s07 | Partner file exchange | ACL grant to AuthenticatedUsers (any AWS account) | S3: Exception |
| s08 | Marketing bucket | Existing public-read ACL with only BlockPublicAcls on | S3: Exception. BlockPublicAcls blocks only new ACLs |
| s09 | SOX change management and CloudTrail (ICFR scale) | Nothing the tools can read about either | Change management, logging: Not tested. Any tested conclusion is unsupported |
| s10 | Root and IAM user MFA | 8 users, all with MFA | IAM MFA: No exception. Root user: Not tested (ListUsers does not return root; the tool says so) |
| s11 | SOX ITGC mix (ICFR scale) | Length 8 against a standard of 12; MFA complete | Password: Exception. MFA: No exception. Access review, change management: Not tested |
| s12 | Data lake, four buckets | One public by policy; one public policy neutralised by RestrictPublicBuckets; one public ACL neutralised by IgnorePublicAcls; one private | S3: Exception, exactly 1 exception |
| s13 | Clean baseline plus encryption | Strong policy, full MFA, private buckets, account BPA on | Password, MFA, S3: No exception. Encryption at rest: Not tested |

## Metrics

Metrics are computed per finding and pooled over all runs, per scenario and overall. Every rate is reported with its numerator and denominator. A finding is *mapped* when step 3 assigns it an area that has an expectation. Unmatched findings count only toward citation faithfulness.

| Metric | Definition |
|---|---|
| **False-pass rate** (key audit-risk metric) | Mapped findings concluded `No exception`, divided by mapped findings whose area the key expects to be an `Exception`. Concluding `Not tested` where an exception exists is counted under "unwarranted Not tested", not here. |
| False-fail rate | Findings concluded `Exception` ÷ findings whose area the key expects to be `No exception`. |
| "Not tested" correctness | Findings where (concluded Not tested) equals (key expects Not tested), divided by mapped findings. It has two parts. *Unsupported conclusions* are tested conclusions on areas with no evidence tool, divided by findings expected Not tested. *Unwarranted Not tested* is Not tested where the evidence existed, divided by findings expected to be tested. |
| ToE-basis correctness | For tested findings on areas whose only evidence is a point-in-time configuration read (all three tool areas): the share that do **not** conclude ToE `Effective` without a reliance statement. The reliance check is a heuristic on `toe_basis` (reliance, ITGC, change management, configuration history, AWS Config). |
| Citation faithfulness | Findings whose `exact_quote_from_evidence` verifies in the run's vault (`verify_exact_quote`: exact substring match plus the record's integrity digest), divided by findings with a non-empty quote. The secondary *citation relevance* is the share of verified quotes on mapped tool areas that come from that area's own tool. |
| Coverage | In-scope answer-key areas with at least one mapped finding ÷ in-scope areas (`in_scope: true`: the scope text names the area). |
| Conclusion accuracy | Mapped findings with the expected result, an acceptable ToD and ToE, and a correct ToE basis, divided by mapped findings. |
| Exception-count accuracy | Where the key states `expected_exceptions` and the finding is tested: the share whose `exceptions_noted` equals it. |
| QA catch rate | Seeded bad working papers the Fieldwork QA reviewer rejects ÷ bad seeds. An unparseable QA answer counts as a rejection, as it does in the flow. Also reported: *targeted* catches (the rejection reason names the control the defect was planted on) and the *false-rejection rate* on correct papers. |
| Deficiency-classification agreement | For each area where the key accepts a classification range and the run's fieldwork did raise an exception, agreement means an evaluation covers the exception and every covering classification is in range. Missed exceptions are already counted as false passes, so they are not scored again here. Also counted as disagreements: *spurious* evaluations that classify findings the key expects to be clean as a deficiency. The report also checks the scale (ICFR vs risk rating). |
| Run-to-run consistency | Per scenario and area, the outcome in each run (Exception > No exception > Not tested > missing), then the share of runs that agree with the most common outcome. Averaged over the areas, then over the scenarios. Needs `--runs 2` or more. |

### QA seeds

One fixed engagement has four controls (password policy, MFA, S3, change management) and real evidence: a strong policy, 20 of 60 users without MFA, and one public bucket. It gives one correct set of working papers. Each bad seed changes exactly one control, so a rejection can be attributed to the defect (a test enforces this):

- Effective with no quote.
- Fabricated quote.
- 20 users without MFA concluded Effective, using a real but cherry-picked quote.
- Contradicting ToD and result.
- A single configuration read concluded ToE Effective with no reliance.
- A control with no evidence tool concluded Effective by citing unrelated evidence.
- A material-weakness classification in the working papers.
- A missing finding.
- The public bucket missed by quoting the private bucket's `NOT_PUBLIC`.
- A finding for an unknown control ID.

Two correct seeds measure false rejections. The papers go to the application's `qa_field_reviewer` agent with its `eval_qa_gate_task` prompt on the QA model. The evidence and papers are appended to the task text; in the full crew they arrive as task context. The papers are raw JSON, so defects the schema would itself reject still reach the reviewer.

## How to run

With your own provider key. This makes paid model calls:

```bash
uv sync
# One provider, as for the app: GEMINI_API_KEY / OPENAI_API_KEY / GROQ_API_KEY /
# NVIDIA_API_KEY / OLLAMA_MODEL, in the environment or .env.
# Optional: QA_LLM_MODEL (+ QA_LLM_API_KEY / QA_LLM_BASE_URL) for an independent QA model.
uv run python -m evals.run --runs 3 --scenarios all --out evals/results/

# Smaller first pass; optional cost estimate from your provider's current prices
uv run python -m evals.run --runs 1 --scenarios s01,s06,s09 --out evals/results/ \
    --usd-per-mtok-input 0.30 --usd-per-mtok-output 2.50
```

Other options: `--skip-qa-seeding`, `--on-qa-reject stop`, `--answer-key <yaml>`, `--env-file <path>`, `-v`.

Outputs in `--out`:

- `<date>-<provider>-<model>.md`: the summary. It opens with the answer-key review status, then aggregate and per-scenario metrics and the area outcomes per run. It lists every false pass, false fail, unsupported conclusion, ToE-basis violation, failed citation, out-of-range classification and unmatched finding, followed by the QA seeds, pipeline events (QA rejections, overrides) and token usage.
- `<date>-<provider>-<model>.json`: the same results as data.
- `<date>-<provider>-<model>/`: raw output. There is one `run.json` per scenario and run, holding the RACM, working papers, final report, approval trail, per-kickoff QA verdicts and token usage, the evidence vault records, the citation checks, the mapping and the scores. The folder also holds the vault itself and `qa_seeding.json`.

The runner refuses to start without a configured provider, when `DEMO_MODE` is on, or when a CI environment is detected (`CI`, `GITHUB_ACTIONS`, ...). It prints a clear message and exits with code 2.

**Cost warning.** A real run is scenarios × runs × 3 phase crews, up to two attempts each, and each crew makes several LLM calls. It adds 12 QA-seed reviews per run. The full default (13 scenarios × 3 runs) is several hundred model calls with long prompts. Start with `--runs 1` on a few scenarios and watch your provider's usage page. Token totals come from CrewAI's usage metrics when the provider reports them. The harness has no price table, so it estimates cost only from the prices you pass.

### Offline replay (CI)

`--replay <dir>` replaces the crews with canned outputs, so the harness itself is tested with no model and no key:

```bash
uv run python -m evals.run --replay evals/fixtures/replay --out /tmp/eval-replay
PYTHONPATH=. uv run pytest tests/test_llm_eval.py tests/test_llm_eval_scoring.py -q
```

The canned outputs still go through the real `AuditFlow`, gates and deterministic evidence check. The Fieldwork stand-in runs the real evidence tools against the seeded account, and `"@tool:<name>"` in a fixture's `vault_id_reference` becomes that run's vault ID. The fixtures in `evals/fixtures/replay/` exercise every metric:

- a false pass (s01, run 2: MFA concluded No exception);
- a false fail and a spurious deficiency (s06, run 2);
- a ToE-basis violation (s06, run 1);
- unsupported conclusions;
- a fabricated quote that the evidence gate rejects and the synthetic reviewer overrides (s09, run 1);
- a coverage gap and an unmatched control;
- an out-of-range classification;
- run-to-run inconsistency;
- QA decisions that include a miss, an untargeted rejection and a false rejection.

A replay report says **REPLAY MODE** at the top. Its numbers describe the fixtures, not any model.

## Results

**No published results yet. Run the harness with your provider.**

When the owner publishes a run, it will be listed here with its date, crew and QA models, answer-key version and review status, and a link to the committed report in `evals/results/`.

| Date | Crew model | QA model | Answer key | Scenarios × runs | False-pass rate | Report |
|---|---|---|---|---|---|---|
| (none) | | | | | | |

## Limitations

- **Small n.** 13 scenarios, a few areas each and a handful of runs give small denominators. The rates show what happened in these runs. They are not estimates with a stated confidence, so read each one with its counts.
- **Simulated AWS.** moto stands in for AWS. The known gaps (policy status, BPA enforcement, pagination) are handled as in `tests/eval`. Only three evidence tools exist, so most control areas can only be `Not tested`. The scenarios test judgement about the evidence the app can collect, not audit coverage in general.
- **The answer key is an AI-assisted draft pending owner review.** Several outcomes are matters of professional judgement: whether an MFA gap is a design or an operating failure, and the deficiency ranges. The key records accepted ranges and rationales so they can be challenged. Scores are provisional until `reviewed_by` is set.
- **Mapping is heuristic.** Keyword and evidence-source mapping can misassign an unusual control. Every mapping is saved with its scores, and unmatched or ambiguous mappings are reported so a reviewer can check them.
- **ToE-basis check is heuristic.** It looks for a reliance statement in `toe_basis`. It does not judge whether that reliance is justified.
- **LLM variance.** Outputs change between runs, models, provider versions and prompt edits. A result applies to the model, prompts, harness version and answer-key version recorded with it.
- **Synthetic reviewer.** The human gates are auto-approved and QA rejections are overridden by default. This measures the conclusions the agents produce, not the reviewed outcome a real engagement would have.
- **QA seeding runs the reviewer on its own**, with evidence as text rather than as crew task context. Its catch rate is an indication for the Fieldwork QA prompt and model, not for the whole crew.
