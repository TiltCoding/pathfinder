# Phases — what to do in each stage (`/test`)

Each stage ends by updating `state.json` (phase, iteration, checkpoint) and `dashboard.json`. Spawn
sub-agents with the Agent tool; pass them the slug and the absolute workspace path. Keep the human's
dashboard truthful. `/test` produces **only tests** (`tests/**`) — never a change to the code under test.

## 1. INTAKE

Goal: capture the target and stand up the workspace.

- Resolve the **target**: a module/file (`scripts/queue.py`), an area, or "the uncovered branches of X".
  Write `brief.md` (goal: what to cover and why; scope/non-scope; any constraints like "no network",
  "only the error paths"). In queue mode the brief is given — adopt it, skip elicitation.
- Create `state.json` (`phase:"INTAKE"`, `iteration:0`), record `baseCommit = git rev-parse HEAD`,
  resolve the run language (`lang`), start the server, copy the dashboard, print the URL (see
  `feedback-loop.md`). Advance to ANALYZE.

## 2. ANALYZE (autonomous)

Goal: find what is worth testing and what is already covered.

- Spawn **`wf-explorer`** (one, or several in parallel for an area — one per file/sub-module) to map the
  target read-only: its public contracts, branches, edge cases, error paths, and which of them existing
  `tests/*.py` already cover. Output → `exploration.md` (a **gap list**: `path:line` → branch/contract →
  covered? → how it could be tested). Each explorer reads `docs/knowledge/INDEX.md` first and skims the
  existing `tests/` to learn the house style.
- Consolidate the explorers' gap lists yourself (dedup overlapping gaps). Update `dashboard.json` (a
  short "analyzing coverage" summary) and advance to PLAN.

## 3. PLAN (autonomous)

Goal: turn the gap list into a concrete, reviewable test plan.

- Spawn **`wf-planner`** with the consolidated gap list. It produces a **test plan** — one block per
  proposed test (or per `tests/test_*.py` file): *what it covers* (the cited branch/contract), *how it
  asserts* (the meaningful assertion, not a tautology), *fixtures* (tempfile/fake-handler/etc. per the
  convention), and the target file name. Plus `questions.md` — open decisions for the human (e.g. "mock
  the subprocess or skip on no-git?", "one big test file or split per concern?").
- Write the plan into `dashboard.json` as `planBlocks[]` (stable ids `plan-1…plan-N`) and the open
  decisions as `questions[]`. Set status `awaiting-batch`. Advance to PLAN GATE.

## 4. PLAN GATE (the one human gate)

Goal: the human reviews and approves the test plan. Batched-feedback loop, exactly like `/feature`.

- **Park** and wait (see `feedback-loop.md`). On a new `submissions/<n>.json`: apply every comment/answer
  (drop a test, add one, change how it asserts, answer an open question), refine the plan, bump
  `iteration`, re-park, and write a short `replies.json` entry per item.
- On **«Утвердить план»** (`approve-plan`): record the approved plan + answers in `state.json` and
  advance to IMPLEMENT.
- **Autonomous drain / eval:** skip the park — self-resolve the open questions with sensible defaults
  (project conventions → lowest-risk → smallest scope that meets the brief; record the rationale) and
  auto-approve. VERIFY is kept.

## 5. IMPLEMENT (autonomous)

Goal: write the approved tests.

- Split the plan into independent work-streams (e.g. one `tests/test_*.py` per concern). For each, spawn
  a **`wf-coder`** that writes the test file(s) **strictly under `tests/`** following
  `docs/knowledge/conventions.md` §tests: offline stdlib `unittest`, the `sys.path` shim, `tempfile` +
  `addCleanup`, cross-platform guards. The coder reads the existing `tests/test_*.py` to match style, and
  reuses fakes/harnesses already there (e.g. the fake-handler in `test_conditional_get.py`,
  `test_hub.py`). It must **not** edit the module under test. Run independent coders in parallel; mark
  each work-stream `done` as it lands. Spawn **`wf-documenter`** in parallel to start the knowledge note.
- Each coder runs the relevant tests for its slice as it goes (`python dev.py test tests.<module>`).
  Update `dashboard.json` work-streams/progress. Advance to VERIFY.

## 6. VERIFY (autonomous)

Goal: prove the tests green and genuine.

- Run the full suite: `python dev.py test` — it must be **green** (a green run is the acceptance gate).
  Also run `python scripts/check_stdlib.py` (the new tests stay stdlib-only).
- Spawn **`wf-reviewer`** to review the new tests for genuineness: each test exercises the cited branch
  and would **fail on a regression** (no tautologies, no assertion-free smoke); fixtures are isolated
  (tempfile, no real network/git/disk-outside-tmp); cross-platform. Fix-or-justify any finding (a coder
  re-spin for a weak test).
- **If a test caught a real bug** in the code under test: STOP — do not fix the code (that is a
  `/feature`). Surface it (chat + a `task-log` note); per the brief either `xfail`/skip the test with a
  documented reason and an escalation, or hard-block for the human (autonomous mode: a destructive/
  behaviour question is a hard-block — ask). Advance to DONE only when the suite is green and the new
  tests are genuine.

## 7. DONE

- Final `dashboard.json` summary: the test files added, what each covers, the green-run result.
- **`wf-documenter`** grows `docs/knowledge/` (see `knowledge-guide.md`): a `task-log.md` entry (what was
  covered and why), and — if the run established a new test pattern (a new fixture/harness) — a short
  note in `conventions.md` or the area doc, with `INDEX.md` refreshed.
- Set `phase:"DONE"`. In queue mode, mark the queue item `done` (`scripts/queue.py done <slug>`) and tell
  the human the drain continues (`/clear` + `/feature`, or the `/loop`). Otherwise point the human at the
  dashboard + the new tests.
