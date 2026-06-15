# Dashboard guide — the `dashboard.json` render model

The dashboard page (`index.html`, a copy of the template) is static and **data-driven**: it polls
`GET /data?slug=<slug>` (your `dashboard.json`) and `GET /replies?slug=<slug>` every few seconds and
re-renders. So to "update the dashboard" you simply rewrite `dashboard.json`. Never hand-edit the HTML.

Write `.workflow/tasks/<slug>/dashboard.json` after every stage. Schema:

```json
{
  "slug": "ask-dashboard-answer-flow",
  "title": "Как ответ агента попадает на дашборд?",
  "phase": "ANSWER",
  "status": "awaiting-batch",
  "summary": "Markdown: развёрнутый ответ на вопрос со ссылками на код (path:line).",
  "planBlocks": [
    { "id": "ans-1", "title": "Поток данных", "body": "Markdown: подтема ответа со ссылками." }
  ],
  "demo": {
    "kind": "diagram",
    "intro": "Markdown: что показывают визуализации.",
    "selectionId": "answer-view",
    "variants": [
      { "id": "process", "title": "Схема процесса решения", "file": "process.svg", "caption": "Как мы пришли к ответу." },
      { "id": "infographic", "title": "Инфографика ответа", "file": "infographic.html", "caption": "Ключевые факты и числа." }
    ]
  },
  "updatedAt": "2026-06-15T10:20:00"
}
```

(The `codebaseMap`/`workstreams`/`progress`/`questions` fields from the `/feature` schema are optional
and unused by `/ask` — there is no plan gate and no choice questions. `/ask` uses `summary` + optional
`planBlocks` for the text answer and `demo` for the two visualizations.)

Field notes:

- **`status`**: `working` (you are researching/synthesizing) or `awaiting-batch` (parked on the chat in
  the `ANSWER` stage, waiting for a follow-up). The header badge reflects this, so keep it honest — it's
  how the human knows whether a chat message will be seen promptly.
- **`phase`**: one of INTAKE / RESEARCH / SYNTHESIZE / ANSWER / DONE.
- **Markdown** is supported in `summary` and block `body` (headings, lists, `code`, **bold**, links).
  Keep the answer self-contained and scannable, with clickable `path:line` references — the human can
  select any text and comment, so write prose worth quoting.
- **Comment anchors**: the human selects a fragment anywhere on the page and the comment is keyed to the
  enclosing region — a `planBlocks[].id` (`ans-K`), the literal `summary` anchor, or a `demo` variant's
  `id`. Each comment carries the quoted `selectedText`. A reply you write under the same anchor renders
  inline beneath that block/card. (For `/ask` the human steers mainly through the **chat panel**, not
  batched comments — see below — but the comment machinery still works if they highlight a fragment.)
- **Stable ids** are essential: `planBlocks[].id` and the `demo` variant `id`/`selectionId` must stay
  constant across re-renders, because comments and your replies are keyed to them. Reuse ids when you
  refine the answer after a follow-up; only mint a new id for a genuinely new sub-topic.
- **`updatedAt`**: bump it every write — the page uses it (plus phase/status/reply-count) to detect
  changes and re-render. Bump it whenever a follow-up updates the answer or re-draws the `demo`.

## The two visualizations (via `demo`)

`/ask` reuses the existing `demo`/`mockups` mechanism to render the **infographic** and the **process
diagram** — with **zero edits to `server.py` or `dashboard.html`** (the backend is content-agnostic;
ADR-0008/0013). The contract:

- **`demo` shape:** `{ "kind": "diagram", "intro": "<markdown>", "selectionId": "answer-view",
  "variants": [ { "id", "title", "file", "caption" } ] }`. `kind` is **`diagram`** (an
  architecture/flow/infographic for backend work, not a UI mockup). `intro` (markdown) says what the
  visualizations show.
- **Two variants, both self-contained files in `<task>/mockups/`:**
  - `process.svg` — **«Схема процесса решения»**: how the answer was reached — knowledge → code →
    reasoning → answer. A static SVG you draw from the consolidated `## Шаги рассуждения` /
    `## Опорные источники` of the digests. SVG is supported out of the box (`image/svg+xml`).
  - `infographic.html` — **«Инфографика ответа»**: the key facts/numbers/relations from `## Числа/связи`
    — KPIs, a block-diagram of the data path, the supporting files. Self-contained HTML, **inline CSS**,
    **dark dashboard style**, **no CDN/external network** (it renders in a sandboxed iframe
    `sandbox="allow-scripts"`).
- **File names must match `MOCKUP_RE = ^[A-Za-z0-9._-]{1,64}\.(html|svg)$`** — Latin letters/digits and
  `._-` only, extension `.html` or `.svg`. No Cyrillic, no spaces, no path traversal. Each `file` is
  served read-only by `GET /mockup?slug=<slug>&file=<file>` into the sandboxed iframe.
- **Cosmetic reuse (no HTML edits).** The card is rendered with the fixed title **«Демо решения»** and a
  radio-per-variant («выбор варианта» + «Отправить на доработку»). For `/ask` this is **reused exactly as
  is** — the two visualizations show as two selectable variants and the human can comment on either one's
  `caption`. The radio is semantically a "pick one" control, but functionally it just records a `choice`
  answer keyed to `selectionId`; `/ask` doesn't need that answer, so ignore it. Renaming the card or
  dropping the radio is an optional future polish (one additive render branch in `dashboard.html`) — not
  for the MVP. Keep `selected` unset (or pointing at the last frozen variant) and don't depend on it.

The human's comments come back to you via `submissions/<n>.json`, and your answers go out via
`replies.json`, which the page shows inline under the matching block/variant.

Keep the model lean: show the human the answer and its two visualizations, not a transcript of
everything you read.

## The chat panel (free-form follow-up questions)

A slide-in panel (💬 Чат in the header) backed by `chat.jsonl` — this is **the primary steering channel
for `/ask`**. After the first answer you park in the `ANSWER` stage listening for the human's follow-up
questions here. A human message wakes you via a `chat` signal; you answer by appending `role:"agent"`
lines (a simple clarification inline, a substantive new question via a fresh mini-swarm + an updated
answer). It is the heart of the `ANSWER` loop — see «Chat» / the ANSWER loop in `feedback-loop.md`.

## The «Документация» tab (automatic — you don't write it)

The page has a **«Документация»** tab served by `GET /knowledge?slug=<slug>`, which shows the project's
`docs/knowledge/` tree read-only. For `/ask` this lets the human browse the same knowledge base your
researchers read first — you do nothing to populate it.

## The «Трейсинг» tab (automatic — you don't write it)

The page has a second tab, «Трейсинг», that visualizes the run's observability data: a session summary
(sub-agents, output tokens, total time, peak context, ≈cost), a parallelism timeline (one lane per
concurrent sub-agent — the branching view shows your parallel researchers as siblings), and a card per
sub-agent with model, duration, output tokens, context-window fill %, cache-hit %, and ≈cost. It is
served by `GET /trace?slug=<slug>`, computed on demand by joining the telemetry spans
(`telemetry.jsonl`) with per-sub-agent transcript usage on disk (only numbers are read, never prose).
You do nothing to populate it — it fills in as researchers run. Note: this **live** trace is *not* the
same as the static `process.svg` you draw at SYNTHESIZE — the diagram explains the answer's reasoning,
the trace shows the run's execution.
