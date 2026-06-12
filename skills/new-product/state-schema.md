# `state.json` — resumable workflow state

One file per task at `.workflow/tasks/<slug>/state.json`. You read it at the start of every
`/new-product` invocation to **resume** exactly where you left off, and you rewrite it whenever
something meaningful changes (stage transition, new iteration, phase status, a consumed submission).

This is the feature schema plus the greenfield fields the evolutionary build-loop needs (`projectRoot`,
`prd`, `build`). Stages and the empty-tree base commit are the canonical constants from `plan.md`
«Контекст» — keep them identical to `phases.md`.

```json
{
  "slug": "cli-pomodoro-timer",
  "title": "CLI-таймер «помодоро» с историей сессий",
  "phase": "BUILD",
  "iteration": 3,
  "checkpoint": "working",
  "createdAt": "2026-06-12T10:00:00",
  "updatedAt": "2026-06-12T11:20:00",
  "projectRoot": "./cli-pomodoro-timer",
  "questions": [
    { "id": "q1", "text": "Где хранить историю сессий?", "answer": "JSON-файл в ~/.pomodoro" }
  ],
  "answers": [],
  "prd": {
    "file": "prd.md",
    "approved": true,
    "frIds": ["FR-1", "FR-2", "FR-3", "FR-4", "FR-5"]
  },
  "build": {
    "currentPhase": "p2",
    "iteration": 3,
    "judge": {
      "dimensions": ["correctness", "UX-clarity", "robustness"],
      "passThreshold": 80,
      "scale": "0-3"
    },
    "phases": [
      {
        "id": "p0",
        "title": "Walking skeleton: запуск CLI + smoke",
        "status": "done",
        "frIds": ["FR-1"],
        "exitCriteria": "CLI запускается, выводит help, smoke-тест зелёный",
        "testSpec": "tests/test_smoke.py — запуск без аргументов выводит usage и rc 0",
        "frozenTests": [
          { "path": "tests/test_smoke.py", "hash": "9f2a…c1" }
        ],
        "budget": { "maxIterations": 5 },
        "scratchpad": "",
        "scoreHistory": [
          { "iteration": 1, "weightedTotal": 88, "verdict": "pass", "testsPassed": 3, "testsFailed": 0 }
        ]
      },
      {
        "id": "p2",
        "title": "Таймер обратного отсчёта + пауза/сброс",
        "status": "in_progress",
        "frIds": ["FR-2", "FR-3"],
        "exitCriteria": "Отсчёт, пауза, сброс работают; тесты зелёные; судья ≥ 80; нет blocking",
        "testSpec": "GWT: дано 25-мин таймер, when старт → then тик каждую секунду; when пауза → then остановка",
        "frozenTests": [
          { "path": "tests/test_timer.py", "hash": "3b7d…e0" }
        ],
        "budget": { "maxIterations": 5 },
        "scratchpad": "scratchpad/p2.md",
        "scoreHistory": [
          { "iteration": 1, "weightedTotal": 0, "verdict": "block", "testsPassed": 4, "testsFailed": 2 },
          { "iteration": 2, "weightedTotal": 71, "verdict": "revise", "testsPassed": 6, "testsFailed": 0 }
        ]
      }
    ]
  },
  "subagents": [
    { "type": "np-coder", "workstream": "p2", "bg": true, "startedAt": "..." }
  ],
  "baseCommit": "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
  "lastSubmission": 2,
  "lastSignalCount": 1,
  "lastChatTs": "2026-06-12T11:15:00",
  "serverPort": 8473
}
```

## Field notes (shared with `/feature`)

- **`phase`** / **`checkpoint`**: `checkpoint` is `working` while you act and `awaiting-batch` while
  parked waiting for a human batch. Together with `phase` they tell a resumed session what to do next.
  `phase` here is a **workflow stage** (below), not a build phase.
- **`lastSubmission`**: the highest `submissions/<n>` you have already consumed. Compare against
  `submit.flag.latest` to detect a new batch.
- **`lastSignalCount`**: how many `signals.json` entries you have already accounted for. Serves as the
  `/wait` long-poll baseline (`sinceSignal`) and keeps you from re-processing old signals.
- **`lastChatTs`**: timestamp of the last `chat.jsonl` message you have already read/answered. On each
  checkpoint wake-up you reply to messages newer than this, then advance it (see `feedback-loop.md`).
- **`subagents`**: lightweight record of what you spawned (especially background `np-coder`s) so a
  resumed session knows what is in flight. In BUILD the `workstream` is the **build-phase id** (`p2`).
- **`questions`**: keep ids stable and store the resolved `answer` once known — this is your record of
  decisions (also feed notable ones to the knowledge base as ADRs).

## Workflow stages (`phase`)

The greenfield orchestrator marches through these stages (canonical — identical in `SKILL.md`,
`phases.md`, and here):

```
INTAKE → DISCOVER → PRD → PRD-GATE → PHASE-PLAN → PLAN-GATE → BUILD → SHIP → DONE
```

«Stage» = a step of the workflow; «phase» = a slice of the product built **inside** BUILD. In BUILD,
telemetry is tagged `workstream=<build-phase id>`, `iteration=<loop iteration>`.

## Greenfield fields

- **`projectRoot`** — where the product's code lives, decided at INTAKE by the scaffold policy:
  the repo root when it is practically empty, otherwise a subdir `./<slug>/`. All `np-coder` writes and
  the product's own `docs/knowledge/` are rooted here. (The `.workflow/` workspace stays at the repo
  root regardless.)
- **`prd`** — `{ file, approved, frIds[] }`. `file` is the artifact path (`prd.md`); `approved` flips
  true on the `approve-plan` signal at **PRD-GATE**; `frIds[]` is the master list of functional-
  requirement ids (`FR-1`…), the spine that every phase, test, and rubric row traces back to.
- **`build`** — the evolutionary loop's whole state:
  - **`currentPhase`** — id of the build-phase in flight (matches a `phases[].id`).
  - **`iteration`** — the current loop iteration **within** `currentPhase`. (Mirror it into the
    top-level `iteration` too, which is what the dashboard badge reads.)
  - **`judge`** — the rubric shape fixed at PHASE-PLAN: `dimensions[]` (one isolated `np-judge` call
    per dimension — names are per-phase), `passThreshold` (`80` of 100 by default), `scale` (`"0-3"`
    per criterion). See `loop.md` and `templates/artifacts/judge-verdict.md`.
  - **`phases[]`** — the ordered build-phases (Ф0 walking skeleton, then vertical slices). Per phase:

    | Field | Meaning |
    |---|---|
    | `id` | stable phase id (`p0`, `p1`, …) — also the telemetry `workstream` and the dashboard `workstreams[].id`. |
    | `title` | short human label. |
    | `status` | `todo` \| `in_progress` \| `done` \| `escalated`. Source of truth for BUILD progress; mirror into `dashboard.json`. |
    | `frIds[]` | the FR ids this phase delivers (subset of `prd.frIds`). |
    | `exitCriteria` | the phase's done-definition (the exit checklist from `phase-plan.md`). |
    | `testSpec` | the thinker's test specification (GWT → cases) the `np-coder` materializes in tests-first mode. |
    | `frozenTests[]` | `[{ path, hash }]` — the test files captured **after** materialization; the coder may never edit these (orchestrator checks `git diff` on these paths each iteration). |
    | `budget` | `{ maxIterations }` — the per-phase iteration cap (default `5`; `2` under `AIPF_EVAL=1`). |
    | `scratchpad` | path to the phase's Reflexion scratchpad (`templates/artifacts/iteration-scratchpad.md`) — distillate only. Empty string before the first REFINE. |
    | `scoreHistory[]` | `[{ iteration, weightedTotal, verdict, testsPassed, testsFailed }]` — one row per iteration, the source of the score-trend table and plateau/oscillation detection. |

  `status: "escalated"` marks a phase parked on a stop-condition (budget / plateau / oscillation)
  awaiting a human choice (see `loop.md` §STOP_*/ESCALATE).

## Greenfield-git (INTAKE rule)

The diff tab (`/changes`) needs a `baseCommit`. For a brand-new product there may be no repo, or a repo
with zero commits:

- **No repository** → run `git init` (in `projectRoot`), then proceed as below.
- **`git rev-parse HEAD` fails** (a repo exists but has **0 commits**) → set
  `baseCommit = 4b825dc642cb6eb9a060e54bf8d69288fbee4904` (the git **empty-tree** hash). The server then
  diffs the working tree against the empty tree, so «Изменения» works from the very first commit.
- **Otherwise** (commits exist) → `baseCommit = git rev-parse HEAD`, exactly as `/feature` does.

This empty-tree case is the one the feature schema does not cover — it is the mandatory greenfield fix.
The hash must stay byte-identical to the one in `phases.md`.

## Resume semantics

On resume: load this file; if `checkpoint === "awaiting-batch"`, re-check `submit.flag`/`signals.json`
before doing anything else; otherwise continue the current `phase` (stage) from where the state says.

In BUILD, a resumed session **re-enters at an iteration boundary** — never mid-iteration. The boundary
is fully described by `build.currentPhase` + `build.iteration` (+ the stage `BUILD`): you restart that
iteration from a clean coder context rather than trying to recover an in-flight one. The frozen tests,
scratchpad, and `scoreHistory` for that phase are the durable inputs that let a fresh `np-coder` pick
up exactly where the loop left off.

## Related files (not part of `state.json`)

- **`.workflow/active.json`** — `{ slug, updatedAt }`, rewritten on every start/resume. Lets the
  telemetry hooks map a Claude Code session to the active task for session-level events.
- **`.workflow/tasks/<slug>/telemetry.jsonl`** — append-only event log written by the hooks (and any
  `POST /telemetry` markers). The **trace id is the slug**, so a task is one Langfuse trace across
  sessions. In BUILD, spans carry `workstream=<build-phase id>` and `iteration=<loop iteration>`.
  Gitignored under `.workflow/`; you don't edit it by hand.
