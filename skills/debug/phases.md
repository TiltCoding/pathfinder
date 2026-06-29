# Phases — what to do in each stage (`/debug`)

Each stage ends by updating `state.json` (phase, iteration, checkpoint) and `dashboard.json`. Spawn
sub-agents with the Agent tool; pass them the slug and the absolute workspace path. Keep the human's
dashboard truthful. `/debug` lands the **smallest** change that corrects one reported defect, guarded by
a regression test — never an opportunistic refactor or feature.

## 1. INTAKE

Goal: capture the symptom and stand up the workspace.

- Record the **symptom**: the exact error / stack trace, the command or input that triggers it, and the
  observed-vs-expected behaviour. Write `brief.md` (the defect, how to trigger it, expected result;
  scope/non-scope; constraints). In queue mode the brief is given — adopt it, skip elicitation.
- Create `state.json` (`phase:"INTAKE"`, `iteration:0`), record `baseCommit = git rev-parse HEAD`,
  resolve the run language (`lang`), start the server, copy the dashboard, print the URL (see
  `feedback-loop.md`). Advance to REPRO.

## 2. REPRO (autonomous)

Goal: reproduce the defect deterministically — the anchor everything else hangs on.

- Establish a **failing reproduction**: ideally a focused failing test (`tests/test_*.py` or a scratch
  script under the workspace) that exercises the reported path and asserts the *expected* result, so it
  is **red** now and becomes the regression guard later; or, when a test isn't yet possible, a documented
  command + observed-vs-expected captured in `repro.md`.
- If the symptom **cannot** be reproduced from what the human gave, do not theorise blindly — record what
  you tried in `repro.md`, set a short note on the dashboard, and ask the human (chat) for the missing
  detail (input, version, env). Only advance once the defect reproduces. Update `dashboard.json` and
  advance to DIAGNOSE.

## 3. DIAGNOSE (autonomous)

Goal: find the **root cause**, not the symptom.

- Spawn **`wf-explorer`** (one, or several in parallel — one per suspect subsystem/hypothesis) to trace
  the failing path read-only and form **competing root-cause hypotheses**, each tied to concrete
  `path:line` evidence and explaining *why* it produces the observed symptom. Output → `exploration.md`
  (per hypothesis: cause → evidence `path:line` → why it explains the repro → confidence → proposed
  minimal fix). Each explorer reads `docs/knowledge/INDEX.md` first.
- Consolidate the hypotheses yourself (dedup, rank by evidence). Spawn **`wf-planner`** with the leading
  hypothesis to produce a minimal **fix plan** — the precise change (the one `path:line` to correct and
  how) **plus** the regression test that will guard it — and `questions.md` (open decisions, e.g. "fix at
  the call site or in the helper?", "is the wider N a separate bug?").
- Write into `dashboard.json` as `planBlocks[]` (stable ids `plan-1…plan-N`): block 1 = the confirmed
  cause + reproduction, the rest = the proposed minimal fix and its regression test. Open decisions →
  `questions[]`. Set status `awaiting-batch`. Advance to ROOT-CAUSE GATE.

## 4. ROOT-CAUSE GATE (the one human gate)

Goal: the human confirms the cause and approves the minimal fix. Batched-feedback loop, like `/feature`.

- **Park** and wait (see `feedback-loop.md`). On a new `submissions/<n>.json`: apply every comment/answer
  (correct the hypothesis, change where/how to fix, answer an open decision, narrow/!widen scope), refine
  the plan, bump `iteration`, re-park, and write a short `replies.json` entry per item. If the human
  rejects the hypothesis, loop back to DIAGNOSE (spawn another explorer on the new lead) before re-gating.
- On **«Утвердить план»** (`approve-plan`): record the confirmed cause + approved fix + answers in
  `state.json` and advance to FIX.
- **Autonomous drain / eval:** skip the park — self-confirm the best-evidenced hypothesis (record the
  rationale) and auto-approve. VERIFY is kept. A **destructive/irreversible** fix (data loss, schema/
  contract break, history rewrite) is a **hard-block even here** — raise it to the human and wait.

## 5. FIX (autonomous)

Goal: apply the smallest corrective change + its regression guard.

- Spawn a **`wf-coder`** with the approved plan to (a) write/finalise the **regression test** so it is red
  against the current code, then (b) make the **minimal** change that addresses the root cause so the test
  goes green. The change touches only what the cause requires — no drive-by refactors. The coder follows
  `docs/knowledge/conventions.md` (and §tests for the regression test), reads the existing code/tests to
  match style, and reuses fixtures/harnesses already there. Spawn **`wf-documenter`** in parallel to start
  the knowledge note.
- The coder runs the relevant tests for its slice as it goes. Update `dashboard.json` work-streams/
  progress. Advance to VERIFY.

## 6. VERIFY (autonomous)

Goal: prove the fix correct and guarded.

- Confirm the regression test **fails before** and **passes after** the change (the red→green proof — if
  it was green before the fix, it isn't guarding the bug; send the coder back). Run the full suite:
  `python dev.py test` — it must be **green**. Run `python scripts/check_stdlib.py` (stdlib/no-CDN stays
  clean). Run the project's review gates as a normal run does (`/code-review`, and `/security-review` when
  the fix touches a security-relevant path) — fix-or-justify high-severity findings.
- Spawn **`wf-reviewer`** to confirm the change actually addresses the *cause* (not just the symptom),
  stays minimal, and that the regression test genuinely guards it. Fix-or-justify any finding (a coder
  re-spin). Advance to DONE only when the repro is fixed, the regression test guards it, and the suite is
  green.

## 7. DONE

- Final `dashboard.json` summary: the root cause, the minimal change, the regression test, the green-run
  result.
- **`wf-documenter`** grows `docs/knowledge/` (see `knowledge-guide.md`): a `task-log.md` entry (the bug,
  its cause, the fix), and — if the defect exposed a **non-obvious invariant** (a latent contract, a
  cross-platform trap) — a short ADR or area-doc note with `INDEX.md` refreshed, so the next agent doesn't
  reintroduce it.
- Set `phase:"DONE"`. In queue mode, mark the queue item `done` (`scripts/queue.py done <slug>`) and tell
  the human the drain continues (`/clear` + `/feature`, or the `/loop`). Otherwise point the human at the
  dashboard + the fix.
