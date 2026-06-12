# The evolutionary build loop — one phase at a time

This is the core of BUILD. For each phase `k` (in order, Ф0 first) you run a **tests-first pre-loop**,
then a sequence of **iterations** until the phase resolves to PASS / STOP_* / ESCALATE. The mechanism
is a hybrid gate: **frozen tests are the wall** (a phase never closes on red tests) and the **judge is
the steering wheel** (it grades how well the slice satisfies the FRs, with evidence). You — the
orchestrator — drive the loop and compute the verdict deterministically; the LLM agents only generate
code and score. Everything human-facing (verdicts, scratchpad rendered to the dashboard, escalation
questions) is **Russian**; these instructions are English.

State you read/write lives in `state.build.phases[k]` (see `state-schema.md`):
`frozenTests[]`, `budget.maxIterations`, `scratchpad`, `scoreHistory[]`, `status`.

## 1. Phase pre-loop — materialize and freeze the tests

Before any implementation exists, lock the target:

1. Spawn `np-coder` (opus) in **tests-first mode** with a **fresh context**, handing it **only the
   thinker's test spec** for phase `k` (the GWT → cases from `phase-plan.md`) — **not** any
   implementation plan. It writes executable tests against the (not-yet-built) product surface.
2. Run the tests. A **red baseline is expected and correct** here — there is no implementation yet.
   (If they pass with no code, the test spec is vacuous: bounce it back to `np-thinker` to tighten.)
3. **Freeze:** record each test file's path **and content hash** in
   `state.build.phases[k].frozenTests` (`[{path, hash}]`). These define "done" for the phase and must
   not change while the phase is being implemented.

## 2. Iteration anatomy — implement → freeze-check → test → judge

Each iteration `i` of phase `k`:

1. **Build the brief-digest (you).** Assemble a compact brief for the coder — never the whole history:
   - the phase **goal** and its **FR-IDs** + **GWT**,
   - the **latest test results** (failing cases, output),
   - the **judge's fix-instructions** from the previous iteration (if any),
   - a **distillate of the scratchpad** (what was tried, what to change),
   - the standing rule: **do not touch the frozen test files**.
2. **Implement.** Spawn `np-coder` (opus) in **implement mode** with that brief and a fresh context. A
   Self-Refine micro-cycle inside the coder is allowed (it may run the tests locally to iterate), but
   **only you commit**, and only on PASS.
3. **Freeze-check (you).** Run `git diff` over the frozen test paths (and re-hash them). If a frozen
   test changed → **revert** that file to its frozen content and treat it as an **ESCALATE** signal
   (the coder broke the contract); do not score this iteration.
4. **Run the tests (you).** Execute the phase's test suite and capture pass/fail counts + output.
5. **Judge — but only if green.** If tests are **red**, skip the judge entirely (the feedback is the
   test output; see `decision()`). If tests are **green**, spawn **3 `np-judge` (opus) in parallel**,
   **one call per rubric dimension** — each judge sees the code/diff and the PRD slice and returns a
   verdict object for its one dimension (`templates/artifacts/judge-verdict.md`): per-criterion `score`
   0–3 + **evidence** (`file:line` / test output) + a **fix-instruction**, plus any `blocking_issues`
   and `unknowns`. A judge may mark a criterion `Unknown` when it lacks evidence; without evidence it
   does **not** award points.
6. **Merge the verdicts (you).** Combine the three into one record: compute `weighted_total` (0–100)
   from the per-criterion `score × weight`; union the `blocking_issues`; carry the `unknowns`. Criteria
   left `Unknown` count as zero contribution (never as a plus). Append the merged record to
   `reviews.json` and to `scoreHistory[]`.

## 3. `decision()` — the deterministic verdict (you compute, not an LLM)

```
decision(tests, judge, iteration, history, budget):
    if tests.failed > 0:                      return REFINE   # judge was NOT called; feedback = test output
    if judge.blocking_issues:                 return REFINE   # green but the judge found a blocker
    if tests.passed_all
       and judge.weighted_total >= 80
       and judge.no_criterion_at_zero:        return PASS
    if iteration >= budget.maxIterations:     return STOP_BUDGET
    if plateau(history):                      return STOP_PLATEAU
    if oscillation(history):                  return ESCALATE
    return REFINE
```

Helper definitions (from the plan's loop defaults):

- `no_criterion_at_zero` — no per-criterion `score == 0` in the merged verdict.
- `plateau(history)` — checked from the 3rd scored iteration on: the best `weighted_total` improved by
  **< 3 points over the last 2 iterations**.
- `oscillation(history)` — **3 verdicts in a row with no new maximum and the sign of the score delta
  flipping** (up-down-up / down-up-down). Anti-thrash guard → human.
- A **frozen-test violation** (step 3) short-circuits to ESCALATE regardless of the above.

Evaluation order matters: red tests → REFINE first (so the judge is never consulted on a red build),
then blocking, then PASS, then the three stop/escalate conditions.

## 4. REFINE — learn and iterate

- Append a **scratchpad** entry to `state.build.phases[k].scratchpad`:
  `{iteration, what_failed, hypothesis_why, what_to_change}` — **you** distill it from the test output
  and the judges' fix-instructions (`templates/artifacts/iteration-scratchpad.md`). Keep it a
  **distillate**: no raw logs or diffs, just the reasoning that should carry into the next attempt.
- Write the merged verdict to `reviews.json` (shape in `feedback-loop.md`).
- Update the dashboard `summary` with the **score trend** (iteration | weighted_total | verdict |
  tests pass/fail) and bump the `iteration` badge.
- Start iteration `i+1` with a **fresh coder context** built from the new brief-digest (step 2.1).

## 5. PASS — close the phase

- `git add -A && git commit` the phase's work (you are the only committer). Use a clear Russian commit
  subject naming the phase.
- Set `phases[k].status = done`, mark the matching `workstreams[]` entry done, bump `progress`
  (phases done/total), and archive the phase's scratchpad in state.
- **V1 gate policy:** advance straight to phase `k+1` with no human gate. If there is no next phase,
  BUILD is complete → advance to SHIP (see `phases.md`).

## 6. STOP_* / ESCALATE — park and ask the human

When `decision()` returns `STOP_BUDGET`, `STOP_PLATEAU`, or `ESCALATE`:

- Set `phases[k].status = escalated`, status `awaiting-batch`, and park (`feedback-loop.md`). Raise a
  **choice-question** in `questions[]` (the "loop escalation = choice-question" pattern) offering:
  - **«+N итераций»** — extend the budget and resume the loop,
  - **«Re-scope фазы»** — send the phase back to `np-thinker` to rewrite its goal / test spec / rubric,
    then re-run the tests-first pre-loop,
  - **«Принять как есть»** — accept the best attempt and PASS the phase, or
  - **«Прервать»** — stop the workflow.
- Attach the **best attempt so far** (the highest-`weighted_total` iteration), the **scratchpad**, and
  the **score history** so the human decides with full context.
- Apply the human's choice on the next batch and continue.

## 7. Default budgets & eval cap

- **Defaults** (from the plan constants): ≤5 iterations per phase; plateau = best `weighted_total`
  improving < 3 points over 2 iterations; oscillation = 3 verdicts without a new max with the delta
  sign flipping; judge = 3 dimensions × scale 0–3, `PASS_THRESHOLD = 80`, no criterion at 0; **red
  tests ⇒ the judge is never called** and the phase never closes on red tests.
- **Headless / eval mode** (`AIPF_EVAL=1`): hard **cap 2 iterations per phase**; STOP_* / ESCALATE
  auto-resolve to **«принять как есть»** (accept best); gates auto-pass. This guarantees a finite,
  unattended run for benchmarking.
