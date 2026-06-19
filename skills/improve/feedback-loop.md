# Feedback loop — companion server + batched checkpoints

The human and you communicate through a small local server and the per-task dashboard. The human can
write comments **any time**; you read them **only at checkpoints**, in batches. This keeps you from
polling during active work and gives the human a calm "queue up edits, then send" experience.

## Starting the server (once per project)

The server is `${CLAUDE_PLUGIN_ROOT}/scripts/server.py` (stdlib only). Start it **in the background**
from the project root so it survives across your turns:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/server.py" --root "$(pwd)" >/dev/null 2>&1
```

Run it with the Bash tool using `run_in_background: true`. **The launch is idempotent** — if a live
server is already serving this project root, the new process prints `reusing live server …` and exits
without binding a second port, so it is always safe to just run the command (no need to probe `/health`
first). It writes `.workflow/server.json` with the chosen `port`, `url` and `root`, and **refreshes a
`ts` heartbeat** every few seconds so a reader can tell a live server from a corpse. Read that file to
learn the port — the port is **stable per project root** (derived from it), so the dashboard URL doesn't
drift between runs. To reap stale/orphaned servers for this root, run the same script with `--gc`.

Then per task:
1. Copy `${CLAUDE_PLUGIN_ROOT}/templates/dashboard.html` → `.workflow/tasks/<slug>/index.html`.
2. Give the human the URL: `http://localhost:<port>/?slug=<slug>`.

The dashboard renders from `dashboard.json` and `replies.json`, which **you** write (see
`dashboard-guide.md`). The server persists the human's side: `draft.json` (accumulating),
`submissions/<n>.json` (a sent batch), `signals.json` (e.g. `approve-plan`), `submit.flag`.

## Signals the dashboard can raise

`signals.json` is an append-only log; the buttons in the dashboard `POST /signal` to it and wake your
`/wait`. Recognized signals:

- **`approve-plan`** — at `/improve`'s **SELECT GATE** the human reuses the «Утвердить план» button; you
  interpret it as **"dispatch the picked features"** (collect the «Делаем» answers from the latest
  submission and advance to DISPATCH — see `phases.md` §PROPOSE/SELECT GATE and `dashboard-guide.md`
  §SELECT GATE). The button text is fixed in the HTML; the meaning is per-workflow (same single-gate
  pattern as `/feature`/`/new-product`, ADR-0007).

(`/improve` has no VERIFY phase: it does not implement code, so there is no diff to review and no
`reviews.json`. The drained `/feature` runs do their own review in their own (fresh-context) sessions.)

## Parking at a checkpoint and consuming a batch

When you reach a checkpoint (the SELECT GATE, where you want the human to pick features):

1. Set `dashboard.json` status to `awaiting-batch` and write it. Tell the user in chat, briefly, that
   the dashboard is ready and they can comment and click «Отправить агенту на доработку» (or «Утвердить
   план» at the gate). Record your baselines in `state.json`: `lastSubmission = submit.flag.latest`
   and `lastSignalCount = len(signals.json.signals)`.
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
   same `/feature` prompt, in case the server or curl dies. If the human is clearly active in chat
   instead, just proceed from chat input.
   - On wake, read `.workflow/tasks/<slug>/submit.flag`. If `latest > lastSubmission`, read
     `submissions/<latest>.json` and process it (below).
   - Read `signals.json`. If an `approve-plan` (or other relevant) signal arrived past `lastSignalCount`,
     act on it (at the SELECT GATE: dispatch the picked features); update `lastSignalCount`.
   - **Read `chat.jsonl`.** If there are `role:"human"` messages newer than `state.json.lastChatTs`,
     handle them (see «Chat» below); update `lastChatTs`.
   - If nothing new (a rare spurious return), re-park (repeat steps 1–2).
3. **Processing a submission:** for each item (`kind: "comment"` with `blockId`+`selectedText`, or
   `kind: "answer"` with `questionId`), apply it. At the SELECT GATE the anchors are the **`feat-K`**
   ids: a `comment` on a `feat-K` card means "refine this feature's description"; an `answer` on the
   `feat-K` choice is the human's pick (`«Делаем»`/`«Пропускаем»`). A comment's `blockId` is the anchor
   of the commented region: a feature-card id (`feat-K`) or a prose-section anchor (`summary`);
   `selectedText` is the exact fragment the human highlighted — use it to locate what they mean. Учти,
   что `answer` по `questionId` может содержать **свободный текст вне `options`** (человек написал свою
   формулировку, напр. «делаем, но без X») — прими его как ответ и как уточнение к брифу этой фичи, не
   отбраковывай. Then append a reply to `replies.json` keyed by the same `blockId`/`questionId` with a
   one- to two-sentence Russian note on what you did. Update `lastSubmission`, bump `iteration`, rewrite
   `dashboard.json` (status back to `working`, then to `awaiting-batch` for the next round).

`replies.json` shape:

```json
{ "replies": [
  { "blockId": "feat-3", "text": "Учёл уточнение: делаем экспорт без BOM. Поправил описание фичи.", "ts": "..." },
  { "questionId": "feat-1", "text": "Принято: фичу 1 диспетчим.", "ts": "..." }
] }
```

## Chat — free-form steering at checkpoints

Alongside the batched feature-pick comments, the dashboard has a **chat panel** for free-form steering
that isn't tied to a feature card — questions, nudges, scope changes, "also look at X". It coexists with
batches. Handle it at checkpoints, the same cadence as everything else.

- Storage: `.workflow/tasks/<slug>/chat.jsonl`, append-only, one JSON object per line
  `{ "role": "human"|"agent", "text": "...", "ts": "...", "phase": "..." }`. A human message also raises
  a `chat` signal, so your parked `/wait` returns immediately.
- On wake (at the SELECT GATE), read messages with `ts > state.json.lastChatTs`. For each: answer it by
  **appending your own `role:"agent"` line** to `chat.jsonl` (the panel renders it), and if it asks for a
  change, fold it into the top-K cards just like a steering batch. Then set `lastChatTs` to the newest
  message ts.
- Keep replies short and in Russian. If a request is large enough to reshape the shortlist, say so in
  chat and reflect it in `dashboard.json` rather than silently diverging.
- In headless/eval mode there is no chat; skip it.

### Anchored discussion (ветки на блоках)

A `chat.jsonl` message may carry an **`anchor`** — a feature-card id (`feat-K`), the literal `summary`,
or a demo-variant id — plus an optional **`quote`** (the exact fragment the human selected). Such a
message renders as a **threaded discussion under that card/region** instead of in the free-form chat
panel. This is now the **per-block discussion channel** — it replaces the old draft-comment cards.

- **Reply in context** by appending your own `chat.jsonl` line with the **same `anchor`** — your reply
  lands in that thread:

  ```json
  {"role":"agent","text":"Учёл уточнение: фичу 3 делаем без BOM.","anchor":"feat-3","ts":"<iso>"}
  ```

- **If your reply is a question back to the human**, add **`"needsAnswer": true`**. The page marks that
  card as having an open thread and raises a header counter «🔸 N ждут ответа», which stays up until the
  human posts a later turn on that anchor. Omit `needsAnswer` for a plain reply — the page then shows
  «✓ учтено агентом».
- `needsAnswer` is **agent-only**: the human's POST never sets it (it only appears on a `role:"agent"`
  line). So a card is "ждёт ответа" exactly when your latest anchored turn asked something and the human
  hasn't answered yet.

## Don't busy-wait

The `/wait` long-poll is already the no-busy-wait path: you block on a background curl and are
re-invoked only when there's a real event, so you burn zero turns while parked and pick up clicks
near-instantly. Do **not** add a short-interval `ScheduleWakeup` to poll on top of it — keep only the
long (~1850s) fallback. If you are waiting on a long background sub-agent (a scout or voter) instead of
the human, you will be re-invoked when it finishes — schedule only a long fallback in that case too.

## Eval / headless mode

With `--eval` / `AIPF_EVAL=1`: do not park for the human. If the fixture pre-seeded
`submissions/*.json`, apply them in order; then treat the SELECT GATE as approved (auto-pick the top-K)
and continue to DISPATCH. This is what lets the workflow run unattended for benchmarking.
