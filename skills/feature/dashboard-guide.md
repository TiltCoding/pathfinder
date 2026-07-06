# Dashboard guide — the `dashboard.json` render model

The dashboard page (`index.html`, a copy of the template) is static and **data-driven**: it polls
`GET /data?slug=<slug>` (your `dashboard.json`) and `GET /replies?slug=<slug>` every few seconds and
re-renders. So to "update the dashboard" you simply rewrite `dashboard.json`. Never hand-edit the HTML.

Write `.workflow/tasks/<slug>/dashboard.json` after every phase/iteration. Schema:

```json
{
  "slug": "add-csv-export",
  "title": "Добавить экспорт отчётов в CSV",
  "phase": "ELABORATE",
  "status": "awaiting-batch",
  "iteration": 2,
  "progress": { "done": 1, "total": 4 },
  "summary": "Markdown. Краткая сводка задачи и цели.",
  "codebaseMap": "Markdown. Что нашли в коде: файлы, точки входа, паттерны.",
  "planBlocks": [
    { "id": "b1", "title": "1. Сервис экспорта", "body": "Markdown тело блока плана." }
  ],
  "questions": [
    { "id": "q1", "text": "Разделитель?", "kind": "choice", "options": ["запятая", "точка с запятой"] },
    { "id": "q2", "text": "Нужен ли UTF-8 BOM для Excel?", "kind": "open" }
  ],
  "demo": {
    "kind": "ui",
    "intro": "Markdown — что показано и как выбрать.",
    "selectionId": "demo",
    "selected": "v1",
    "variants": [
      { "id": "v1", "title": "Вариант A — сайдбар слева", "file": "v1.html", "caption": "Markdown — плюсы/компромиссы." },
      { "id": "v2", "title": "Вариант B — таб-бар сверху", "file": "v2.html", "caption": "Markdown." }
    ]
  },
  "workstreams": [
    { "id": "ws1", "title": "Сервис экспорта", "status": "in_progress" }
  ],
  "updatedAt": "2026-06-09T22:20:00"
}
```

Field notes:

- **`status`**: `working` (you are acting) or `awaiting-batch` (parked, waiting for the human). The
  header badge reflects this, so keep it honest — it's how the human knows whether a click will be seen.
- **`phase`**: one of INTAKE / EXPLORE / ELABORATE / PLAN GATE / IMPLEMENT / VERIFY / REVIEW / DONE.
  REVIEW (the code-review wizard, after a green VERIFY) is **non-terminal** — the task stays active in
  the hub while the wizard runs; see the «Ревью» tab below and `phases.md` §6.5.
- **`progress`**: usually work-streams done/total during IMPLEMENT; drives the top bar.
- **`now`** / **`nowAt`** (optional, back-compatible): a one-line **human** description of what you are
  doing right now (e.g. `"исследую server.py"`) plus an ISO timestamp. The header shows it as «Сейчас: …».
  Update both whenever your activity changes so the line stays live; the page greys it out after ~90s
  stale (`nowAt`) and hides it entirely while `status:"awaiting-batch"` (you're parked, not acting).
  Omit both to leave the line off — old `dashboard.json` without these fields renders unchanged.
- **Markdown** is supported in `summary`, `codebaseMap`, and block `body` (headings, lists, `code`,
  **bold**, links). Keep blocks self-contained and scannable — the human comments by selecting any
  text and typing a note, so write prose worth quoting.
- **Comment anchors**: the human selects a fragment anywhere in the plan and the comment is keyed to
  the enclosing region. For a plan block the anchor is its `planBlocks[].id` (`b1`…); for the prose
  cards it is the literal anchor `summary` or `codebaseMap`. Each comment carries the quoted
  `selectedText`. A reply you write under the same anchor (see `feedback-loop.md`) renders inline
  beneath that block/card — so reply with `blockId: "summary"` to answer a comment on the summary.
- **Свой ответ на choice-вопрос**: у вопроса с `kind:"choice"` человек, помимо готовых `options`, может
  ввести **свою формулировку** в поле свободного ответа. Это приходит как обычный `answer` того же
  `questionId`, но его `text` **может не совпадать ни с одной из `options`** — не считай такой ответ
  невалидным, прими свободный текст. На вопрос приходит **один `answer`**: свой ответ перебивает
  выбранную опцию (и наоборот), так что просто читай `answer.text` как ответ человека.
- **Stable ids** are essential: `planBlocks[].id` and `questions[].id` must stay constant across
  iterations, because the human's comments and your replies are keyed to them. Reuse ids when you edit
  a block; only mint a new id for a genuinely new block.
- **`demo`** (optional) — a visual preview of the solution shown as a "Демо решения" card before the
  plan: 2–3 alternatives the human can look at and pick one of. `kind` is `ui` (an interface mockup)
  or `diagram` (an architecture/flow/infographic for backend/CLI work). Each `variants[]` entry names a
  **self-contained HTML/SVG file** (no external network/CDN — it renders in a sandboxed iframe) that
  lives in `.workflow/tasks/<slug>/mockups/<file>` and is served read-only by `GET /mockup`. The human
  **selects** a variant with its radio — this is just a `choice` answer keyed to `selectionId`, so it
  lands in the next batch like any other answer; `selected` pre-highlights the last frozen choice. The
  `caption` is a commentable region (anchor = the variant's `id`), so the human can comment on a variant
  and you reply under it via `replies.json` exactly like a plan block. Variant `id`/`selectionId` are
  stable ids — keep them constant across iterations.
  - **Явное поле комментария к варианту**: у каждого варианта есть всегда видимое поле комментария,
    формирующее `comment` с `blockId = <variants[].id>` и (обычно) пустым `selectedText`. Твой реплай
    в `replies.json` по тому же `blockId` рендерится под этим вариантом — как и раньше работало через
    выделение текста в `caption`, но теперь и у варианта **без `caption`** (раньше footer с реплаями
    появлялся только при наличии caption).
- **`updatedAt`**: bump it every write — the page uses it (plus phase/status/reply-count) to detect
  changes and re-render.

The human's comments come back to you via `submissions/<n>.json` (see `feedback-loop.md`), and your
answers go out via `replies.json`, which the page shows inline under the matching block/question.

Keep the model lean: show the human what they need to decide and steer, not a transcript of everything.

## The «Изменения» tab (changed files + review runs)

A tab that turns the dashboard into a control panel for the change itself. Two parts:

- **Changed files + diff** — served by `GET /changes?slug=<slug>`, computed on demand from git: the
  working tree diffed against `state.json.baseCommit` (the `HEAD` you captured at INTAKE; falls back to
  `HEAD` if absent, and reports `notGit` outside a repo). It lists each file with its `+N/−M` counts and
  status (added/modified/deleted/renamed); clicking one fetches its unified diff
  (`/changes?slug=&file=<path>`, path-traversal-guarded). **You write nothing** — it reflects the real
  tree as coders land work.
- **Review runs** — rendered from `reviews.json`, which **you** write in VERIFY. Two buttons let the
  human request `/code-review` or `/security-review`; they raise the `run-code-review` /
  `run-security-review` signals (see `feedback-loop.md`) which you honor at your next checkpoint. Each
  run shows its status, summary, and ranked findings with clickable `file:line` (which opens that file's
  diff). See `phases.md` §VERIFY for when to populate it and `feedback-loop.md` for the JSON shape.

## The «Ревью» tab (the code-review wizard — `review` field)

The 6th tab. After VERIFY is green you publish a **`review`** object into `dashboard.json` (written in
REVIEW — see `phases.md` §6.5); the wizard reads it from `GET /data?slug=<slug>` and pulls each hunk's
diff body on demand from `GET /changes?file=<path>` (the same endpoint the «Изменения» tab uses — the
model never duplicates the diff text). The FE (the tab, the step-by-step walk) lives in the template;
**you write only the `review` field** — the server is agnostic to it, so this is **0 server changes**.

```jsonc
"review": {
  "summary": "markdown — what the feature is, diff size, what to look at first",
  "status": "open",            // "open" | "resolved" (set to "resolved" on «Завершить ревью»)
  "iteration": 1,               // bump each fix round
  "steps": [                    // FILES ranked by importance (array order = ranking)
    { "file": "scripts/server.py", "anchor": "rev:scripts/server.py",
      "status": "modified", "added": 42, "removed": 5,
      "rank": 1, "kind": "logic",           // kind: "logic" | "cosmetic"
      "comment": "markdown — what/why for the file",
      "blocks": [                           // HUNKS ranked by importance
        { "anchor": "rev:scripts/server.py#0",   // "<fileAnchor>#<hunkIdx>", stable across ticks
          "hunkHeader": "@@ -120,7 +120,9 @@ def do_GET",
          "range": [120,128], "rank": 1, "kind": "logic",
          "comment": "markdown — what/why for the hunk" } ] } ]
}
```

- **`steps[]`** are the files you changed, **array order = the importance ranking** (rank 1 first); each
  step's `blocks[]` are its hunks, again ranked. `kind` is `"logic"` (real behavior — public
  contract/API, semantic logic, security/write/parse risk) or `"cosmetic"` (rename, reformat, param
  pass-through, test/fixture). See the combined-importance heuristic in `phases.md` §6.5.
- **`anchor`** is a stable **string keyed to the hunk index** (`rev:<path>` / `rev:<path>#<idx>`), **not**
  the line range — ranges drift between fix iterations, the index doesn't, so a human's anchored thread
  stays attached to the right hunk across ticks. It is also the `chat.jsonl` anchor the human comments on
  and you reply under — see «Review wizard cycle» in `feedback-loop.md`.
- **`comment`** is markdown in the run language (`state.lang`); the header machine keys (`anchor`, `kind`,
  `status`) stay stable/English. **Hunk bodies are not in the model** — the wizard fetches them from
  `GET /changes?file=`.
- **`status`** flips `open` → `resolved` when the human clicks «Завершить ревью» (an `approve-plan`
  signal); **`iteration`** bumps each fix round, and you **re-rank** the affected steps/blocks after
  fixes because the diff moved. Update `updatedAt` on every write.

## The chat panel (free-form steering)

A slide-in panel (💬 Чат in the header) backed by `chat.jsonl` for free-form messages that aren't tied
to a plan block. The human's messages wake you via a `chat` signal; you answer by appending
`role:"agent"` lines. It coexists with batched comments and is consumed at checkpoints — see «Chat» in
`feedback-loop.md`.

## The «Трейсинг» tab (automatic — you don't write it)

The page has a second tab, «Трейсинг», that visualizes the run's observability data: a session summary
(sub-agents, output tokens, total time, peak context, ≈cost), a parallelism timeline (one lane per
concurrent sub-agent — the branching view), and a card per sub-agent with model, duration, output
tokens, context-window fill %, cache-hit %, and ≈cost. It is served by `GET /trace?slug=<slug>`, which
the server computes on demand by joining the telemetry spans (`telemetry.jsonl`) with per-sub-agent
**transcript** usage on disk (only numbers are read, never prose). You do nothing to populate it — it
fills in as sub-agents run. The same token/cost data is also pushed to Langfuse generations when
forwarding is enabled.
