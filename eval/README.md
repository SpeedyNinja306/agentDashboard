# eval

Golden-set test cases and scoring for each worker.

```
eval/harness.py          runner: loads cases, runs the worker, scores, prints the report
eval/scoring.py          case schema, deterministic checks, LLM-as-judge
eval/selftest.py         tests the harness itself; needs no model and no credentials
eval/<worker>/cases.yaml the golden set for that worker
```

## Running

```bash
# score the shipped prompt
AGENTDASH_MODEL=anthropic:claude-sonnet-4-5 python -m eval.harness --worker research-specialist

# score a candidate prompt version without editing the worker
python -m eval.harness --worker research-specialist --prompt-version v2

# check the score is reproducible rather than flaky
python -m eval.harness --worker research-specialist --repeat 3

# narrow to one area while iterating
python -m eval.harness --only adversarial
```

Exit code is 0 when the pass rate meets `--threshold` (default 0.90), nothing errored, and no case
changed verdict across repeats. `--json` emits the full per-case record for CI.

A hosted model is required for a meaningful score. The `offline` backend ignores the system prompt
and returns a canned payload, so it exercises the plumbing only; the harness warns and refuses to
let the offline backend act as judge rather than manufacturing verdicts that look real.

## How cases are scored

Two tiers, chosen per case:

- **`checks`** — deterministic assertions, used wherever the goal has a checkable right answer.
  `contains_all`, `contains_any`, `not_contains`, `regex`, `not_regex`, `min_words`, `max_words`.
- **`judge`** — an explicit PASS/FAIL rubric applied by a model, used only for open-ended goals
  where no assertion can express correctness.

The rubric text lives in the YAML, never in the scoring code, so what counts as a good answer stays
reviewable in a diff. A case passes only if the status matches, every check passes, and the judge
returns PASS.

Outcomes are `pass`, `fail`, and `error`. `error` means the harness could not get a verdict — the
worker crashed, the judge was unavailable, the judge reply was unparseable. It is reported
separately because counting a broken harness as a prompt failure would understate the prompt and
hide the real problem. Errors still count against the pass rate.

## Adding a case

Prefer a deterministic check. Reach for a rubric only when correctness genuinely cannot be
expressed as an assertion, and then write the rubric as numbered PASS requirements plus explicit
FAIL conditions — the schema rejects a rubric that does not state both. Give every case a
`rationale` saying what failure mode it guards, so a later reader can tell whether a change to it
is a fix or a weakening of the set.

When a case carries `checks`, add fixtures to `_DISCRIMINATION` in `eval/selftest.py`: one
realistic output that must pass, and one per check that must fail. An unexercised check is easy to
get wrong in both directions — a misspelled substring passes everything, and a loose regex fails
correct output — and neither shows up as an error. The selftest asserts that every check-bearing
case has fixtures, so adding a case without them fails the suite.

Judge settings are pinned at `temperature=0`. A case whose verdict moves between identical runs
makes the pass rate meaningless, so `--repeat` treats any verdict change as a failure of the run.
