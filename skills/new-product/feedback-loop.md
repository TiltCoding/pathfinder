# Feedback loop — companion server + batched checkpoints

The human and you communicate through a small local server and the per-task dashboard. The human can
write comments **any time**; you read them **only at checkpoints**, in batches. This keeps you from
polling during active work and gives the human a calm "queue up edits, then send" experience. The
**server mechanics are unchanged** from the feature workflow — this file only fixes the greenfield gate
texts, the contextual meaning of `approve-plan`, the loop-escalation pattern, and the judge record
shape. (Buttons are baked into the dashboard HTML; nothing here requires editing the server or HTML.)

## Starting the server (once per project)

The server is `${CLAUDE_PLUGIN_ROOT}/scripts/server.py` (stdlib only). Start it **in the background**
from the project root so it survives across your turns:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/server.py" --root "$(pwd)" >/dev/null 2>&1
```

Run it with the Bash tool using `run_in_background: true`. It writes `.workflow/server.json` with the
chosen `port` and `url` — read that file to learn the port (it auto-picks a free port near 8473). If
`server.json` already exists and `GET /health` answers **and** its `pid`/`port` match `server.json`,
reuse it instead of starting another; otherwise treat `server.json` as stale and start a new server.

Then per task:
1. Copy `${CLAUDE_PLUGIN_ROOT}/templates/dashboard.html` → `.workflow/tasks/<slug>/index.html`.
2. Give the human the URL: `http://localhost:<port>/?slug=<slug>`.

The dashboard renders from `dashboard.json` and `replies.json`, which **you** write (see
`dashboard-guide.md`). The server persists the human's side: `draft.json` (accumulating),
`submissions/<n>.json` (a sent batch), `signals.json` (e.g. `approve-plan`), `submit.flag`.

## Signals the dashboard can raise

`signals.json` is an append-only log; the buttons in the dashboard `POST /signal` to it and wake your
`/wait`. Recognized signals:

- **`approve-plan`** — the human clicked **«Утвердить план»**. The same button serves **both gates**, so
  **interpret it by the current stage** (`state.json.phase`):
  - on **PRD-GATE** → "the **PRD** is approved" (freeze `prd.md`, set `prd.approved`, advance to
    PHASE-PLAN);
  - on **PLAN-GATE** → "the **phase plan** is approved" (freeze `phase-plan.md`, write `build.phases[]`,
    advance to BUILD);
  - on **SHIP** (final acceptance) → "the **product** is accepted" (advance to DONE).
  Ignore it in stages that have no gate.
- **`run-code-review`** / **`run-security-review`** — at SHIP, re-run that review skill (via
  `wf-reviewer`) over the current diff and append a fresh entry to `reviews.json`. Safe to receive in
  any stage; honor it at your next checkpoint.

`reviews.json` carries two kinds of entry. **Review runs** (unchanged):

```json
{ "runs": [
  { "id": "r1", "kind": "code-review", "status": "done", "ts": "...",
    "summary": "Краткий итог.", "findings": [
      { "severity": "high", "file": "src/x.py", "line": 42, "text": "…" } ] } ] }
```

**Judge verdicts** (new — written each scored iteration in BUILD and once at SHIP). A judge entry is a
`runs[]` item with `kind: "judge"` and a deterministic id `judge-<phase>-i<iter>` (SHIP uses
`judge-ship`). Its `summary` is **Russian markdown** carrying the **merged** verdict: the
`weighted_total` (0–100), the per-dimension score table, and the test pass/fail line; `findings` are the
judges' `actionable_critique` ranked by severity (a `blocking_issue` maps to `severity: "high"`):

```json
{ "id": "judge-p2-i3", "kind": "judge", "status": "done", "ts": "...",
  "summary": "**Вердикт: revise — 72/100.**\n\n| Измерение | Балл | Вес |\n|---|---|---|\n| Корректность | 2/3 | 0.5 |\n| Полнота FR | 2/3 | 0.3 |\n| Надёжность | 1/3 | 0.2 |\n\nТесты: 11/12 зелёных.",
  "findings": [
    { "severity": "high", "file": "src/timer.py", "line": 58, "text": "FR-4 не выполняется: пауза не останавливает счётчик." },
    { "severity": "med", "file": "src/cli.py", "line": 22, "text": "Нет обработки отрицательного интервала." } ] }
```

The per-criterion verdict object (`score 0–3`, `evidence`, `fix`, `blocking_issues`, `unknowns`) and the
merge rules live in `templates/artifacts/judge-verdict.md`; the loop (`loop.md`) is what produces and
merges them. The **«Изменения»** tab renders all `runs[]` entries the same way (status, summary, ranked
findings with clickable `file:line`).

## Parking at a checkpoint and consuming a batch

You reach a checkpoint at either gate (PRD-GATE, PLAN-GATE), at SHIP acceptance, or when the build loop
escalates (`loop.md` §STOP / ESCALATE).

1. Set `dashboard.json` status to `awaiting-batch` and write it. Tell the user in chat, briefly, what
   they're looking at and that they can comment and click **«Отправить агенту на доработку»** (or
   **«Утвердить план»** at a gate). Record your baselines in `state.json`:
   `lastSubmission = submit.flag.latest` and `lastSignalCount = len(signals.json.signals)`.
2. **Park on the long-poll, not a timer.** Read `url` from `.workflow/server.json` and start a
   **background** `curl` (Bash tool, `run_in_background: true`) on the `/wait` endpoint:

   ```bash
   curl -sS --max-time 1830 \
     "<url>/wait?slug=<slug>&sinceSubmission=<lastSubmission>&sinceSignal=<lastSignalCount>&timeout=1800" \
     || true
   ```

   `/wait` blocks until a new submission or signal lands and then returns instantly, so the harness
   re-invokes you **the moment the human clicks** — near-zero latency, no idle wake-ups. As a
   **fallback only**, also set a long `ScheduleWakeup` (~1850s, just past the curl timeout) with the
   same `/new-product` prompt, in case the server or curl dies. If the human is clearly active in chat
   instead, just proceed from chat input.
   - On wake, read `.workflow/tasks/<slug>/submit.flag`. If `latest > lastSubmission`, read
     `submissions/<latest>.json` and process it (below).
   - Read `signals.json`. If an `approve-plan`, `run-code-review`, `run-security-review` (or other
     relevant) signal arrived past `lastSignalCount`, act on it **per the current stage**; update
     `lastSignalCount`.
   - **Read `chat.jsonl`.** If there are `role:"human"` messages newer than `state.json.lastChatTs`,
     handle them (see «Chat» below); update `lastChatTs`.
   - If nothing new (a rare spurious return), re-park (repeat steps 1–2).
3. **Processing a submission:** for each item (`kind: "comment"` with `blockId`+`selectedText`, or
   `kind: "answer"` with `questionId`), apply the change. A comment's `blockId` is the anchor of the
   commented region: a plan-block id (`prd-*` / `fr-*` / `phase-*`) **or** a prose-section anchor
   (`summary` / `codebaseMap`); `selectedText` is the exact fragment the human highlighted — use it to
   locate what they mean. At a gate, route content edits through `np-thinker` (it revises `prd.md` /
   `phase-plan.md`); at an escalation, fold the chosen option into the loop. Then append a reply to
   `replies.json` keyed by the same `blockId`/`questionId` with a one- to two-sentence Russian note on
   what you did. Update `lastSubmission`, bump `iteration`, rewrite `dashboard.json` (status back to
   `working`, then to `awaiting-batch` for the next round).

`replies.json` shape:

```json
{ "replies": [
  { "blockId": "fr-4", "text": "Уточнил Given-When-Then для паузы по комментарию.", "ts": "..." },
  { "questionId": "q-esc-p2", "text": "Принято: +2 итерации, цикл возобновлён.", "ts": "..." }
] }
```

## Loop escalation = a choice-question in `questions[]`

When the build loop hits a stop-condition, you surface it as a **choice-question** (not a gate). Add it
to `questions[]` with `kind: "choice"` and a stable id (e.g. `q-esc-<phase>`), options **«+N
итераций» / «Re-scope фазы» / «Принять как есть» / «Прервать»**, and put the best attempt + scratchpad
+ score history into the dashboard `summary` so the human can decide. The human's pick comes back as a
normal `answer` keyed to that question id on the next batch; you apply it per `loop.md` §STOP. This
reuses the existing answer channel — no new server endpoint.

## Chat — free-form steering at checkpoints

Alongside the batched comments, the dashboard has a **chat panel** for free-form steering that isn't
tied to a block — questions, nudges, scope changes, "also do X". It coexists with batches; it does
**not** interrupt a running coder or judge. Handle it at checkpoints, the same cadence as everything
else.

- Storage: `.workflow/tasks/<slug>/chat.jsonl`, append-only, one JSON object per line
  `{ "role": "human"|"agent", "text": "...", "ts": "...", "phase": "..." }`. A human message also raises
  a `chat` signal, so your parked `/wait` returns immediately.
- On wake (or at any checkpoint), read messages with `ts > state.json.lastChatTs`. For each: answer by
  **appending your own `role:"agent"` line** to `chat.jsonl`, and if it asks for a change, fold it into
  the PRD / phase plan / remaining phases just like a steering batch. Then set `lastChatTs` to the
  newest message ts.
- Keep replies short and in Russian. If a request is large enough to reshape the PRD or phase plan, say
  so in chat and reflect it in `dashboard.json` rather than silently diverging.
- In headless/eval mode there is no chat; skip it.

### Anchored discussion (ветки на блоках)

A `chat.jsonl` message may carry an **`anchor`** — a `planBlocks[].id` (a PRD section `prd-*`/`fr-*` or a
phase `phase-*`), the literal `summary` / `codebaseMap`, or a demo-variant id (`v1`…) — plus an optional
**`quote`** (the exact fragment the human selected). Such a message renders as a **threaded discussion
under that block/region/variant** instead of in the free-form chat panel. This is now the **per-block
discussion channel** — it replaces the old draft-comment cards.

- **Reply in context** by appending your own `chat.jsonl` line with the **same `anchor`** — your reply
  lands in that thread:

  ```json
  {"role":"agent","text":"Уточнил Given-When-Then для паузы.","anchor":"fr-4","ts":"<iso>"}
  ```

- **If your reply is a question back to the human**, add **`"needsAnswer": true`**. The page marks that
  block as having an open thread and raises a header counter «🔸 N ждут ответа», which stays up until the
  human posts a later turn on that anchor. Omit `needsAnswer` for a plain reply — the page then shows
  «✓ учтено агентом».
- `needsAnswer` is **agent-only**: the human's POST never sets it (it only appears on a `role:"agent"`
  line). So a block is "ждёт ответа" exactly when your latest anchored turn asked something and the human
  hasn't answered yet.

## Don't busy-wait

The `/wait` long-poll is already the no-busy-wait path: you block on a background curl and are
re-invoked only when there's a real event, so you burn zero turns while parked and pick up clicks
near-instantly. Do **not** add a short-interval `ScheduleWakeup` to poll on top of it — keep only the
long (~1850s) fallback. If you are waiting on a long background coder/judge instead of the human, you
will be re-invoked when it finishes — schedule only a long fallback in that case too.

## Eval / headless mode

With `--eval` / `AIPF_EVAL=1`: do not park for the human. If the fixture pre-seeded
`submissions/*.json`, apply them in order; then treat **both gates** as approved and continue. Loop
escalations auto-resolve to «принять как есть» and the iteration cap is 2 per phase (see `loop.md`).
This is what lets the workflow run unattended for benchmarking.
