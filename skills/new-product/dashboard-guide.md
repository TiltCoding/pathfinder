# Dashboard guide — the `dashboard.json` render model

The dashboard page (`index.html`, a copy of the template) is static and **data-driven**: it polls
`GET /data?slug=<slug>` (your `dashboard.json`) and `GET /replies?slug=<slug>` every few seconds and
re-renders. So to "update the dashboard" you simply rewrite `dashboard.json`. Never hand-edit the HTML.

> **Greenfield note:** `/new-product` reuses the **existing** server and dashboard unchanged. Every
> greenfield concept below maps onto a field that already exists — **no new server endpoint and no HTML
> change is needed.** See «Greenfield mapping» for the exact correspondence.

Write `.workflow/tasks/<slug>/dashboard.json` after every stage/iteration. Schema:

```json
{
  "slug": "cli-pomodoro-timer",
  "title": "CLI-таймер «помодоро»",
  "phase": "BUILD",
  "status": "working",
  "iteration": 3,
  "progress": { "done": 1, "total": 4 },
  "summary": "Markdown. Краткая сводка задачи и цели.",
  "codebaseMap": "Markdown. Что нашли в коде: файлы, точки входа, паттерны.",
  "planBlocks": [
    { "id": "prd-problem", "title": "Проблема", "body": "Markdown тело раздела PRD." }
  ],
  "questions": [
    { "id": "q1", "text": "Где хранить историю?", "kind": "choice", "options": ["JSON-файл", "SQLite"] },
    { "id": "q2", "text": "Нужен ли звуковой сигнал по окончании?", "kind": "open" }
  ],
  "demo": {
    "kind": "ui",
    "intro": "Markdown — 2–3 варианта дизайна продукта, человек выбирает один.",
    "selectionId": "design",
    "selected": "v1",
    "variants": [
      { "id": "v1", "title": "Вариант A — компактный список", "file": "v1.html", "caption": "Markdown — плюсы/компромиссы." },
      { "id": "v2", "title": "Вариант B — доска", "file": "v2.html", "caption": "Markdown." }
    ]
  },
  "workstreams": [
    { "id": "p0", "title": "Walking skeleton", "status": "done" },
    { "id": "p2", "title": "Таймер + пауза/сброс", "status": "in_progress" }
  ],
  "updatedAt": "2026-06-12T11:20:00"
}
```

Field notes:

- **`status`**: `working` (you are acting) or `awaiting-batch` (parked, waiting for the human). The
  header badge reflects this, so keep it honest — it's how the human knows whether a click will be seen.
- **`phase`**: a **workflow stage** — INTAKE / DISCOVER / PRD / PRD-GATE / PHASE-PLAN / PLAN-GATE /
  BUILD / SHIP / DONE (see `state-schema.md`). The page renders whatever string you write.
- **`progress`**: in BUILD, **build-phases done/total**; drives the top bar.
- **`now`** / **`nowAt`** (optional, back-compatible): a one-line **human** description of what you are
  doing right now (e.g. `"генерирую фазу p2"`, `"гоняю тесты"`) plus an ISO timestamp. The header shows
  it as «Сейчас: …». Update both whenever your activity changes so the line stays live; the page greys it
  out after ~90s stale (`nowAt`) and hides it entirely while `status:"awaiting-batch"` (you're parked,
  not acting). Omit both to leave the line off — old `dashboard.json` without these fields renders
  unchanged.
- **Markdown** is supported in `summary`, `codebaseMap`, and block `body` (headings, lists, `code`,
  **bold**, links, tables). Keep blocks self-contained and scannable — the human comments by selecting
  any text and typing a note, so write prose worth quoting.
- **Comment anchors**: the human selects a fragment anywhere in the plan and the comment is keyed to
  the enclosing region. For a plan block the anchor is its `planBlocks[].id`; for the prose cards it is
  the literal anchor `summary` or `codebaseMap`. Each comment carries the quoted `selectedText`. A reply
  you write under the same anchor (see `feedback-loop.md`) renders inline beneath that block/card — so
  reply with `blockId: "summary"` to answer a comment on the summary.
- **Stable ids** are essential: `planBlocks[].id` and `questions[].id` must stay constant across
  iterations, because the human's comments and your replies are keyed to them. Reuse ids when you edit
  a block; only mint a new id for a genuinely new block.
- **`demo`** (optional) — a visual preview shown as a "Демо решения" card. For `/new-product` the
  thinker may offer it at PRD / PHASE-PLAN as **2–3 product-design variants** (UI mockups, or an
  architecture/data-flow diagram) for the human to pick from before the build. `kind` is `ui` or
  `diagram`. Each `variants[]` entry names a
  **self-contained HTML/SVG file** (no external network/CDN — it renders in a sandboxed iframe) that
  lives in `.workflow/tasks/<slug>/mockups/<file>` and is served read-only by `GET /mockup`. The human
  **selects** a variant with its radio — just a `choice` answer keyed to `selectionId`, so it lands in
  the next batch like any other answer; `selected` pre-highlights the last frozen choice. The `caption`
  is a commentable region (anchor = the variant's `id`). Variant `id`/`selectionId` are stable ids.
- **`updatedAt`**: bump it every write — the page uses it (plus phase/status/reply-count) to detect
  changes and re-render.

The human's comments come back to you via `submissions/<n>.json` (see `feedback-loop.md`), and your
answers go out via `replies.json`, which the page shows inline under the matching block/question.

Keep the model lean: show the human what they need to decide and steer, not a transcript of everything.

## Greenfield mapping (where each `/new-product` concept renders)

Every greenfield artifact lands on an existing render model — **nothing new on the server or in the
HTML**. The correspondence:

| Greenfield thing | Renders as | Notes |
|---|---|---|
| **PRD** (at PRD-GATE) | `planBlocks[]` | One block per PRD section. Section ids `prd-*` (e.g. `prd-problem`, `prd-goals`, `prd-fr`); for a per-FR block use `fr-*` (e.g. `fr-3`). The FR table goes in the body of its block as a markdown table. |
| **Phase plan** (at PLAN-GATE) | `planBlocks[]` | One block per build-phase, ids `phase-*` (`phase-0`, `phase-1`, …). Body = goal, FR-ids, exit checklist, test spec, rubric, budget. |
| **Build-phases** (in BUILD) | `workstreams[]` | One entry per product phase; `id` = phase id (`p0`…), `status` mirrors `state.build.phases[].status` (`todo`/`in_progress`/`done`/`escalated`). |
| **Phases done/total** | `progress` | `{ done, total }` over the build-phases. |
| **Loop iteration** | `iteration` badge | The current loop iteration of `currentPhase` (= `state.build.iteration`). |
| **Current phase goal + score trend** | `summary` | Markdown: the current phase's goal, then a **score-trend table** built from that phase's `scoreHistory` — columns: итерация \| total \| вердикт \| тесты (e.g. `2 \| 71 \| revise \| 6/0`). |
| **Judge verdicts** | `reviews.json` | One run per (phase, iteration): `id: "judge-p2-i3"`, `kind: "judge"`. See below. |
| **Loop escalations** (stop-conditions) | `questions[]` | A `choice` question offering «+N итераций» / «re-scope фазы» / «принять как есть» / «прервать» — exactly like any other gate question. |

### Judge verdicts in `reviews.json`

The judge writes its merged per-phase verdict into the **same** `reviews.json` the «Изменения» tab
already renders (the shape is in `feedback-loop.md`). For `/new-product`:

- **`id`** — `"judge-<phase>-i<iteration>"`, e.g. `"judge-p2-i3"`.
- **`kind`** — `"judge"` (alongside the existing `code-review` / `security-review`).
- **`summary`** — the verdict + `weighted_total` + a markdown table of the per-dimension scores + one
  line for the test result. Example:

  ```
  revise — 71/100 (порог 80)

  | измерение     | балл | вес | вклад |
  |---------------|------|-----|-------|
  | correctness   | 2    | 0.5 | 33    |
  | UX-clarity    | 2    | 0.3 | 20    |
  | robustness    | 2    | 0.2 | 18    |

  Тесты: 6 зелёных / 0 красных.
  ```

- **`findings`** — the judge's `actionable_critique` (localization → fix), one per item, with a
  `severity`. **`blocking_issues` map to `severity: "high"`**; revise-level notes to `med`/`low`
  (the three classes the dashboard styles: `high`/`med`/`low`).
  Each finding keeps its `file:line` (or test-output reference) so the tab links to the diff.

The diff itself (the changed files) needs nothing from you — the «Изменения» tab computes it on demand
from git against `state.json.baseCommit` (the empty-tree hash for a fresh greenfield repo; see
`state-schema.md`). You write only `reviews.json`.

## The chat panel (free-form steering)

A slide-in panel (💬 Чат in the header) backed by `chat.jsonl` for free-form messages that aren't tied
to a plan block. The human's messages wake you via a `chat` signal; you answer by appending
`role:"agent"` lines. It coexists with batched comments and is consumed at checkpoints — see «Chat» in
`feedback-loop.md`.

## The «Трейсинг» tab (automatic — you don't write it)

The page also has a «Трейсинг» tab that visualizes the run's observability data: a session summary,
a parallelism timeline (one lane per concurrent sub-agent — useful in BUILD when three `np-judge`s run
at once), and a card per sub-agent with model, duration, output tokens, context-window fill %,
cache-hit %, and ≈cost. It is served by `GET /trace?slug=<slug>`, computed on demand from the telemetry
spans joined with per-sub-agent transcript usage (only numbers are read, never prose). You do nothing
to populate it — it fills in as sub-agents run.
