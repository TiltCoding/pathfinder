# Rubric: judge-loop quality (`reviews.json`, `state.json`, scratchpad)

Judge how the evolutionary build-loop of `/new-product` behaved across a run. Each criterion is pass/fail with evidence.

- **Evidence-backed verdict-objects** — judge entries in `reviews.json` (`kind: "judge"`, id `judge-<phase>-i<iteration>`) are structured verdict-objects: per-criterion score 0–3 with a concrete `evidence` ref (file:line or test output) and a `fix` instruction; criteria scored without evidence use the `Unknown` escape, not an invented number.
- **Hybrid gate respected** — no phase is closed on red tests (tests are the wall): when tests fail the judge is not invoked and feedback is the test output; a phase reaches `done` only with green frozen tests.
- **Frozen tests honored** — the phase's frozen test files are unchanged between iterations (no editing the tests to pass); any freeze violation was reverted/escalated, not absorbed.
- **Stop conditions fired (bounded)** — the loop terminated by a real `decision()` outcome (PASS / STOP_BUDGET / STOP_PLATEAU / ESCALATE) and respected the in-prompt cap (≤2 iterations/phase, ≤2 phases); it did not run unbounded or loop forever.
- **Scratchpad is a distillate** — the iteration scratchpad holds `{iteration, what_failed, hypothesis_why, what_to_change}` distilled by the orchestrator, not raw logs/diffs pasted in.
- **Score trend recorded** — `state.json` `scoreHistory` (and the dashboard summary trend) tracks weighted_total / verdict / tests per iteration, so progress (or plateau) is auditable.
- **Judges isolated** — verdicts reflect one-dimension-per-call judging merged by the orchestrator (3 dimensions, weights, threshold 80), not a single conflated score.

A healthy loop is auditable and terminating: every closed phase has green frozen tests plus an evidence-backed passing verdict, and every stop is a named decision.
