# Running workflow evaluations

Goal: measure how much the `ai-pathfinder` plugin helps on real coding tasks, and compare variants of its
components — across different project types. We reuse the **skill-creator** eval toolchain rather than
reinventing it.

Locate skill-creator's scripts (the directory containing `scripts/aggregate_benchmark.py`,
`eval-viewer/generate_review.py`, `agents/grader.md`). Call it `$SC`.

## 1. Prepare fixtures

`fixtures/` holds representative projects of different types (greenfield, existing JS/TS, Python
service). Each fixture has its own test suite and, optionally, `.aipf-seed/submissions/*.json` —
pre-seeded human feedback batches that the headless run consumes so the workflow runs unattended.
Add more fixtures to widen "different projects" coverage; keep each small and self-contained.

## 2. Run cases (with-workflow vs baseline)

For each eval in `evals.json`, run two configurations on a **fresh copy** of the fixture:

- **with_workflow** — Claude with this plugin enabled, headless: `AIPF_EVAL=1`, prompt = the eval
  prompt, auto-approving the plan and applying any `seed_submissions`.
- **baseline** — vanilla Claude on the same prompt, no plugin.

Save outputs under `<workspace>/iteration-<N>/<eval-name>/{with_workflow,baseline}/` following the
skill-creator layout, and capture `timing.json` (tokens, duration) from each run's notification.

## 3. Grade

Two kinds of assertion (see `evals.json`):
- **`kind: "script"`** — objective checks: run the fixture's test suite (`pytest`, `npm test`, …),
  grep for the expected route/symbol, check that `docs/knowledge/INDEX.md` and `task-log.md` changed.
  Prefer a reusable script over eyeballing.
- **`kind: "llm"`** — quality judged against `rubrics/` by a grader subagent (`$SC/agents/grader.md`).
  Write results to `grading.json` per run using fields `text`, `passed`, `evidence`.

## 4. Aggregate + view

```bash
python -m scripts.aggregate_benchmark <workspace>/iteration-N --skill-name feature   # in $SC
nohup python $SC/eval-viewer/generate_review.py <workspace>/iteration-N \
  --skill-name feature --benchmark <workspace>/iteration-N/benchmark.json >/dev/null 2>&1 &
```

This gives pass_rate / time / tokens with mean ± stddev and the delta, plus the Outputs/Benchmark viewer.

## 5. Compare components (the point of all this)

To answer "is variant X better?", change **one** component and re-run, holding fixtures/prompts fixed.
Useful axes:
- `agents/wf-planner.md` prompt v1 vs v2,
- EXPLORE reading `docs/knowledge/` vs ignoring it (measures the flywheel),
- number of parallel `wf-explorer`s,
- model per sub-agent.

Run several seeds per config so `aggregate_benchmark.py` can show variance; use `$SC/agents/comparator.md`
for a blind A/B and `$SC/agents/analyzer.md` to explain why the winner won.

## 6. Trigger optimization

Optimize the `feature` skill description so `/feature` fires on the right requests:

```bash
python -m scripts.run_loop --eval-set evals/trigger-eval.json \
  --skill-path skills/feature --model <session-model-id> --max-iterations 5 --verbose   # in $SC
```

Build `trigger-eval.json` as ~20 realistic should/should-not-trigger queries (substantive, multi-step
coding tasks vs near-miss one-liners), then apply the resulting `best_description`.
