# Dashboard guide — the `dashboard.json` render model

The dashboard page (`index.html`, a copy of the template) is static and **data-driven**: it polls
`GET /data?slug=<slug>` (your `dashboard.json`) and `GET /replies?slug=<slug>` every few seconds and
re-renders. So to "update the dashboard" you simply rewrite `dashboard.json`. Never hand-edit the HTML.

Write `.workflow/tasks/<slug>/dashboard.json` after every phase/iteration. Schema:

```json
{
  "slug": "improve-dashboard",
  "title": "Аудит и улучшение дашборда",
  "phase": "PROPOSE",
  "status": "awaiting-batch",
  "iteration": 1,
  "summary": "Markdown. Как выбирать: отметьте «Делаем»/«Пропускаем» по каждой фиче, затем Отправить → Утвердить.",
  "planBlocks": [
    { "id": "feat-1", "title": "Кэшировать /changes", "body": "Markdown: призма / проблема / изменение / объём·риск·impact / файлы." }
  ],
  "questions": [
    { "id": "feat-1", "text": "Кэшировать /changes?", "kind": "choice", "options": ["Делаем", "Пропускаем"] }
  ],
  "updatedAt": "2026-06-13T10:20:00"
}
```

(At the SELECT GATE each top-K feature is **one `planBlocks[]` card + one `questions[]` choice with the
same `id = feat-K`** — see §SELECT GATE below. The `demo`/`codebaseMap`/`workstreams`/`progress` fields
from the `/feature` schema are optional and unused by `/improve`'s gate.)

Field notes:

- **`status`**: `working` (you are acting) or `awaiting-batch` (parked, waiting for the human). The
  header badge reflects this, so keep it honest — it's how the human knows whether a click will be seen.
- **`phase`**: one of INTAKE / SCOUT / CONSENSUS / PROPOSE / DISPATCH / DONE.
- **`progress`**: optional; `/improve` may use it at DISPATCH as dispatched/total feature runs.
- **`now`** / **`nowAt`** (optional, back-compatible): a one-line **human** description of what you are
  doing right now (e.g. `"рой сканирует UX"`, `"диспетчу feat-3"`) plus an ISO timestamp. The header
  shows it as «Сейчас: …». Update both whenever your activity changes so the line stays live; the page
  greys it out after ~90s stale (`nowAt`) and hides it entirely while `status:"awaiting-batch"` (you're
  parked, not acting). Omit both to leave the line off — old `dashboard.json` without these fields renders
  unchanged.
- **Markdown** is supported in `summary` and block `body` (headings, lists, `code`, **bold**, links).
  Keep feature cards self-contained and scannable — the human comments by selecting any text and typing
  a note, so write prose worth quoting.
- **Comment anchors**: the human selects a fragment anywhere on the page and the comment is keyed to
  the enclosing region. For a feature card the anchor is its `planBlocks[].id` (`feat-K`); for the prose
  card it is the literal anchor `summary`. Each comment carries the quoted `selectedText`. A reply you
  write under the same anchor (see `feedback-loop.md`) renders inline beneath that block/card — so reply
  with `blockId: "summary"` to answer a comment on the summary.
- **Свой ответ на choice-вопрос**: у вопроса с `kind:"choice"` человек, помимо готовых `options`, может
  ввести **свою формулировку** в поле свободного ответа. Это приходит как обычный `answer` того же
  `questionId`, но его `text` **может не совпадать ни с одной из `options`** — не считай такой ответ
  невалидным, прими свободный текст. На вопрос приходит **один `answer`**: свой ответ перебивает
  выбранную опцию (и наоборот), так что просто читай `answer.text` как ответ человека.
- **Stable ids** are essential: `planBlocks[].id` and `questions[].id` must stay constant across
  iterations, because the human's comments and your replies are keyed to them. At the SELECT GATE the
  card and its choice **share the same `id = feat-K`** (the binding anchor). Reuse ids when you edit a
  card; only mint a new id for a genuinely new feature.
- **`demo`** (optional, rarely used by `/improve`) — a visual preview shown as a "Демо решения" card:
  2–3 alternatives the human can look at and pick one of. `kind` is `ui` (an interface mockup)
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

## SELECT GATE — the feature-pick contract (`feat-K`)

This is `/improve`'s one gate. The human picks which top-K candidates to dispatch. It reuses the
existing `questions[kind:"choice"]` + `approve-plan` machinery with **zero edits to `server.py` or
`dashboard.html`** (the recommended Variant A, ADR-0008). The contract:

- **One card + one choice per feature, sharing `id = feat-K`.** For each top-K candidate `K` write:
  - a `planBlocks[]` card `{ "id": "feat-K", "title": "<feature name>", "body": "<markdown>" }` whose
    body carries призма / проблема / предлагаемое изменение / объём·риск·impact / затронутые файлы
    (clickable paths) **plus an obligatory ranking line** from `state.json.votes[]` in the compact form
    `score X.XX · согласие N% · impact·effort·risk a·b·c` (numbers only, no vote-note) — so the ranking
    the orchestrator already computed is visible at the gate (**0 server edits** — it is just markdown in
    the existing `body` field, per ADR-0013). This is the rich context the human reads, and
  - a `questions[]` entry `{ "id": "feat-K", "text": "<feature name>?", "kind": "choice",
    "options": ["Делаем", "Пропускаем"] }` — the binary pick.
  The shared `feat-K` id binds the card to its choice, and also routes comments/replies
  (`repliesByBlock` / `repliesByQ`) to the same anchor.
- **Free-form answers are valid (ADR-0008).** Besides the two radio options, the human may type a
  free-form note in the own-answer field (e.g. «делаем, но без X»). It arrives as a normal `answer` on
  the same `feat-K` with `text` outside `options` — accept it as the pick **and** as a refinement to that
  feature's brief; don't reject it. There is exactly **one `answer` per feature** (radio and own-answer
  override each other), so just read `answer.text`.
- **Default: no answer = Пропускаем.** A feature the human never answered simply doesn't appear in the
  submission (`saveAnswer` ignores empty input). Treat a missing `feat-K` answer as **Пропускаем** — the
  human never has to click every radio, only the ones they want to keep. State this in the `summary`.
- **Mandatory order: Submit → Approve.** `draft.json` is **not** in the server's `READABLE_FILES`, so the
  agent cannot see the picks until they are submitted. The human must click **«Отправить»** first (which
  freezes the choices into `submissions/<n>.json`), **then** **«Утвердить план»** (the `approve-plan`
  signal). If `approve-plan` arrives with no fresh submission, you have no picks to read — re-ask the
  human to Submit first. State this order in the `summary` too.
- **`approve-plan` means "dispatch the picked ones."** The button text «Утвердить план» is fixed in the
  HTML; here it means the human finished picking → take the latest submission, collect every `feat-K`
  whose `answer.text` is «Делаем» (or a free-form "делаем…"), record them in `state.json.selected[]`, and
  advance to DISPATCH (see `phases.md` §DISPATCH, `consensus.md` §DISPATCH).

**Inline-choice render (Variant B) — now implemented.** The gate no longer shows a separate «Вопросы»
card listing every `feat-K` radio twice. The `templates/dashboard.html` render now draws a `choice`
question **inline inside the footer of the `planBlocks[]` card whose `id` it shares** (above
`regionFooter`), via the helper `choiceInput(item, ans, reps)`. The binding is strictly
`planIds.has(id) && kind === "choice"` (it matches the **shared id + choice kind**, not a `feat-` name
prefix), so any card whose id has a matching `choice` question — `feat-K` here — gets its radio rendered
right under its own brief. In the «Вопросы» loop that inlined choice is skipped (`continue`), and the
«Вопросы» card is suppressed entirely when nothing is left to show. This is the additive, content-
agnostic render branch foreseen earlier: **zero `server.py` edits**, `wireBlocks`/`saveAnswer`/
`[data-answer]`/`POST /draft` untouched, so the Variant A contract above (shared `feat-K` id, free-form
answers, no-answer = Пропускаем, Submit → Approve ordering) is unchanged — only its on-screen layout is
nicer (the pick sits with the brief it concerns instead of in a long duplicated-radio list).

**Backward compatibility.** The match is by id **and** `kind === "choice"`, never by the `feat-` prefix,
so it touches nothing else: drain-mode `/feature` runs and the `/feature` // `/new-product` plan gates
(whose `questions` ids are not also `planBlocks` ids, or whose questions are `open`) render exactly as
before. Any `dashboard.json` without an id collision renders unchanged.

## The «Изменения» tab (changed files + diff)

A tab that diffs the working tree against `state.json.baseCommit` — served by `GET /changes?slug=<slug>`,
computed on demand from git (falls back to `HEAD` if `baseCommit` is absent, reports `notGit` outside a
repo). `/improve` itself edits no code, so its own «Изменения» tab is usually empty; the real diffs live
in the **drained** `/feature` runs' own dashboards (each diffed against its own `baseCommit`, taken from
the dispatch queue). `/improve` has no VERIFY phase and writes no `reviews.json`.

## The chat panel (free-form steering)

A slide-in panel (💬 Чат in the header) backed by `chat.jsonl` for free-form messages that aren't tied
to a feature card. The human's messages wake you via a `chat` signal; you answer by appending
`role:"agent"` lines. It coexists with batched comments and is consumed at the SELECT GATE — see «Chat»
in `feedback-loop.md`.

## The «Трейсинг» tab (automatic — you don't write it)

The page has a second tab, «Трейсинг», that visualizes the run's observability data: a session summary
(sub-agents, output tokens, total time, peak context, ≈cost), a parallelism timeline (one lane per
concurrent sub-agent — the branching view), and a card per sub-agent with model, duration, output
tokens, context-window fill %, cache-hit %, and ≈cost. It is served by `GET /trace?slug=<slug>`, which
the server computes on demand by joining the telemetry spans (`telemetry.jsonl`) with per-sub-agent
**transcript** usage on disk (only numbers are read, never prose). You do nothing to populate it — it
fills in as sub-agents run. The same token/cost data is also pushed to Langfuse generations when
forwarding is enabled.
