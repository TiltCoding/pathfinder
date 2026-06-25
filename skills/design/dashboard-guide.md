# Dashboard guide — the `dashboard.json` render model

The dashboard page (`index.html`, a copy of the template) is static and **data-driven**: it polls
`GET /data?slug=<slug>` (your `dashboard.json`) and `GET /replies?slug=<slug>` every few seconds and
re-renders. So to "update the dashboard" you simply rewrite `dashboard.json`. Never hand-edit the HTML.
`/design` uses the **same** render model as `/feature` — **0 server changes**; the only design-specific
shapes are the single annotated `demo` and the per-finding CONSENT GATE.

Write `.workflow/tasks/<slug>/dashboard.json` after every phase/iteration. Schema:

```json
{
  "slug": "design-profile-form",
  "title": "Аудит UI/UX: форма настроек профиля",
  "phase": "CONSENT GATE",
  "status": "awaiting-batch",
  "iteration": 1,
  "progress": { "done": 0, "total": 4 },
  "summary": "Markdown. Какой компонент, как задан (имя/скриншот), что предлагает /design.",
  "planBlocks": [
    { "id": "f1", "title": "1. Контраст и акцент CTA", "body": "Markdown: проблема → предложение → призма, severity, `path:line`." }
  ],
  "questions": [
    { "id": "f1", "text": "Находка 1 — усилить контраст CTA. Применить?", "kind": "choice", "options": ["Применить", "Пропустить"] }
  ],
  "demo": {
    "kind": "ui",
    "intro": "Markdown — один макет на все находки: бейджи ①②③ + легенда + тогл «До/После».",
    "selectionId": "design-demo",
    "variants": [
      { "id": "redesign", "title": "Предлагаемый редизайн (аннотированный)", "file": "redesign.html", "caption": "Markdown — как читать бейджи и легенду." }
    ]
  },
  "workstreams": [
    { "id": "ws1", "title": "Контраст CTA", "status": "todo" }
  ],
  "updatedAt": "2026-06-25T22:20:00"
}
```

Field notes:

- **`status`**: `working` (you are acting) or `awaiting-batch` (parked, waiting for the human). The
  header badge reflects this, so keep it honest — it's how the human knows whether a click will be seen.
- **`phase`**: one of INTAKE / AUDIT / COMPOSE / CONSENT GATE / IMPLEMENT / VERIFY / DONE.
- **`progress`**: approved findings done/total during IMPLEMENT; drives the top bar.
- **`now`** / **`nowAt`** (optional, back-compatible): a one-line **human** description of what you are
  doing right now (e.g. `"свожу находки роя"`) plus an ISO timestamp. The header shows it as «Сейчас: …».
  Update both whenever your activity changes so the line stays live; the page greys it out after ~90s
  stale (`nowAt`) and hides it entirely while `status:"awaiting-batch"`. Omit both to leave the line off.
- **Markdown** is supported in `summary` and block `body` (headings, lists, `code`, **bold**, links).
  Keep finding cards self-contained and scannable — the human comments by selecting any text and typing a
  note, so write prose worth quoting.
- **Comment anchors**: the human selects a fragment anywhere and the comment is keyed to the enclosing
  region. For a finding card the anchor is its `planBlocks[].id` (`f<k>`); for the prose card it is the
  literal anchor `summary`. Each comment carries the quoted `selectedText`. A reply you write under the
  same anchor (see `feedback-loop.md`) renders inline beneath that card — so reply with `blockId:"f1"` to
  answer a comment on finding 1.
- **Свой ответ на choice-вопрос**: у вопроса с `kind:"choice"` человек, помимо «Применить»/«Пропустить»,
  может ввести **свою формулировку** в поле свободного ответа. Это приходит как обычный `answer` того же
  `questionId`, но его `text` **может не совпадать ни с одной из `options`** — не считай такой ответ
  невалидным, прими свободный текст (напр. «применить, но без анимации»). На вопрос приходит **один
  `answer`**: свой ответ перебивает выбранную опцию.
- **Stable ids** are essential: `planBlocks[].id` and `questions[].id` must stay constant across
  iterations, because the human's comments and your replies are keyed to them. A finding's card and its
  choice question **share the same `f<k>` id** — that pairing is the CONSENT GATE contract. Reuse ids
  when you edit a finding; only mint a new id for a genuinely new finding.

## `demo` — ONE self-contained annotated mockup

Unlike `/feature` (2–3 pick-one variants) or `/ask` (two visualizations), `/design`'s `demo` is a
**single** annotated mockup shown as a "Демо решения" card before the findings:

- **One variant only**: `selectionId:"design-demo"`,
  `variants:[{ id:"redesign", file:"redesign.html", … }]`. There is **no pick-one selection** — the
  human doesn't choose between mockups; the per-finding decision happens at the CONSENT GATE. (The radio
  still renders, but there's nothing to compare; you can leave `selected` unset.)
- `variants[0].file` names a **self-contained HTML file** (`redesign.html`) that lives in
  `.workflow/tasks/<slug>/mockups/` and is served read-only by `GET /mockup` in a sandboxed iframe — **no
  external network/CDN**, inline CSS/JS only, `data:` images ok (CSP).
- **Variant A annotation format** (the heart of the demo): the redesigned component carries **numbered
  badges ①②③…** (one per finding, positioned over the element it touches; badge N ↔ finding `f<k>`),
  a **side legend** mapping number → problem → what changed (with a prism tag chip), and a **«До/После»
  toggle** that flips the original look vs the redesigned look. The canonical exemplar ships with the skill
  at `skills/design/redesign.example.html` — copy it and reproduce its
  `.stage`/`.mark`/`aside.legend`/`.toolbar` structure, adapting the badge count and legend rows to this
  run's findings.
- The `caption` is a commentable region (anchor = the variant's `id`, `redesign`), and the variant has an
  always-visible comment field forming a `comment` with `blockId:"redesign"` — your reply in
  `replies.json` under the same `blockId` renders beneath the demo. Variant `id`/`selectionId` are stable
  — keep them constant across iterations.

## CONSENT GATE — the per-finding pick contract (`f<k>`)

The single human gate. Each consolidated finding is rendered as a **`planBlocks[]` ↔ `questions[]`
pair sharing the same `id = f<k>`** (feat-K style, ADR-0013 — **0 server changes**, it reuses the
`questions[choice]` + `approve-plan` contract):

- **`planBlocks[f<k>]`** — the finding card: «N. <короткое имя>», with the problem, the proposal, the
  prism tag(s), severity, and `location` (`path:line`). Write it to read alongside badge N in the demo.
- **`questions[f<k>]`** — `kind:"choice"`, `options:["Применить","Пропустить"]`. The dashboard renders
  it as a radio; the human's pick lands in the next batch as an `answer` keyed to `f<k>`.

Flow and defaults (state these in `summary` for the human):

- **Default «Применить».** The audit recommends applying; the human **unchecks** what they don't want.
  Spell out that **«нет ответа = Применить»** — a finding the human never touched stays in the set (this
  is opt-out, the inverse of `/improve`'s opt-in feature pick).
- **Mandatory order Submit → Approve.** `draft.json` is **not** readable over HTTP, so the picks are only
  visible to you after the human clicks **«Отправить»** (Submit). Only then does **«Утвердить план»**
  (Approve → `approve-plan` signal, interpreted here as "implement the remaining set") freeze the
  approved set. If `approve-plan` arrives with no fresh `submissions/<n>.json`, there's nothing to read —
  ask the human to Submit first.
- On approve, the **approved set** = every finding whose recorded decision is «Применить»; those become
  the `workstreams[]` you implement. Skipped findings are dropped.

The human's comments come back to you via `submissions/<n>.json` (see `feedback-loop.md`), and your
answers go out via `replies.json`, which the page shows inline under the matching finding/question.

Keep the model lean: show the human what they need to decide and steer, not a transcript of everything.

## The «Изменения» tab (changed files + review runs)

A tab that turns the dashboard into a control panel for the change itself. Two parts:

- **Changed files + diff** — served by `GET /changes?slug=<slug>`, computed on demand from git: the
  working tree diffed against `state.json.baseCommit` (the `HEAD` you captured at INTAKE; falls back to
  `HEAD` if absent, `notGit` outside a repo). It lists each file with `+N/−M` counts and status; clicking
  one fetches its unified diff (`/changes?slug=&file=<path>`, path-traversal-guarded). **You write
  nothing** — it reflects the real tree as `ds-coder`s land work.
- **Review runs** — rendered from `reviews.json`, which **you** write in VERIFY. The **«Запустить
  /code-review»** button raises the `run-code-review` signal (see `feedback-loop.md`) which you honor at
  your next checkpoint. Each run shows its status, summary, and ranked findings with clickable `file:line`
  (which opens that file's diff). `/design` runs the `/code-review` gate; see `phases.md` §VERIFY.

## The chat panel (free-form steering)

A slide-in panel (💬 Чат in the header) backed by `chat.jsonl` for free-form messages that aren't tied
to a finding — and the channel the **screenshot attachment** rides in at INTAKE (`images:[…]` on a human
line; see `feedback-loop.md`). The human's messages wake you via a `chat` signal; you answer by appending
`role:"agent"` lines. It coexists with batched comments and is consumed at checkpoints.

## The «Трейсинг» tab (automatic — you don't write it)

The page has a second tab, «Трейсинг», that visualizes the run's observability data: a session summary
(sub-agents, output tokens, total time, peak context, ≈cost), a parallelism timeline (one lane per
concurrent sub-agent — the auditors fan out here), and a card per sub-agent with model, duration, output
tokens, context-window fill %, cache-hit %, and ≈cost. It is served by `GET /trace?slug=<slug>`, which
the server computes on demand by joining the telemetry spans (`telemetry.jsonl`) with per-sub-agent
**transcript** usage on disk (only numbers are read, never prose). You do nothing to populate it.
