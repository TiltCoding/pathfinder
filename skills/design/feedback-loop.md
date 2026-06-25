# Feedback loop — companion server + batched checkpoints

The human and you communicate through a small local server and the per-task dashboard. The human can
write comments **any time**; you read them **only at checkpoints**, in batches. This keeps you from
polling during active work and gives the human a calm "queue up edits, then send" experience. `/design`
rides this **same** server/dashboard contract as `/feature` — **0 server changes**.

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

## Screenshot attachment (INTAKE)

When the component is given by **screenshot**, the human attaches it on the dashboard via the
image-attachment contract (ADR-0020): the page `POST /attach`s the image (base64-in-JSON, ≤5 MB,
png/jpeg/gif/webp), the server writes the bytes under `task_dir/attachments/` with a safe server name,
and the reference rides a `/chat` line (`images:[{file,name,mime}]`). You pick it up at your next
checkpoint by reading `chat.jsonl`, then **`Read` the saved file by its absolute path**
(`.workflow/tasks/<slug>/attachments/<file>`) to get the visual context, and pass that path to the
`ds-auditor`s so they can `Read` it too. This is purely the existing contract — no new server surface.

## Signals the dashboard can raise

`signals.json` is an append-only log; the buttons in the dashboard `POST /signal` to it and wake your
`/wait`. Recognized signals for `/design`:

- **`approve-plan`** — the human approved the finding set at the CONSENT GATE (freeze the «Применить»
  findings, advance to IMPLEMENT). Same button, interpreted as "implement the remaining set".
- **`run-code-review`** — re-run `/code-review` over the current diff and append a fresh entry to
  `reviews.json` (see VERIFY in `phases.md`). Safe to receive in any phase; honor it at your next
  checkpoint.

`reviews.json` shape (you write it; the **«Изменения»** tab renders it):

```json
{ "runs": [
  { "id": "r1", "kind": "code-review", "status": "done", "ts": "...",
    "summary": "Краткий итог.", "findings": [
      { "severity": "high", "file": "src/x.py", "line": 42, "text": "…" } ] } ] }
```

## Parking at a checkpoint and consuming a batch

When you reach a checkpoint (the CONSENT GATE, or an IMPLEMENT checkpoint where you want human input):

1. Set `dashboard.json` status to `awaiting-batch` and write it. Tell the user in chat, briefly, that
   the dashboard is ready and they can comment and click «Отправить» (or «Утвердить план» at the gate).
   Record your baselines in `state.json`: `lastSubmission = submit.flag.latest` and
   `lastSignalCount = len(signals.json.signals)`.
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
   same `/design` prompt, in case the server or curl dies. If the human is clearly active in chat
   instead, just proceed from chat input.
   - On wake, read `.workflow/tasks/<slug>/submit.flag`. If `latest > lastSubmission`, read
     `submissions/<latest>.json` and process it (below).
   - Read `signals.json`. If an `approve-plan`, `run-code-review` (or other relevant) signal arrived
     past `lastSignalCount`, act on it; update `lastSignalCount`.
   - **Read `chat.jsonl`.** If there are `role:"human"` messages newer than `state.json.lastChatTs`,
     handle them (see «Chat» below); update `lastChatTs`. (This is also where a screenshot attachment
     arrives — see above.)
   - If nothing new (a rare spurious return), re-park (repeat steps 1–2).
3. **Processing a submission:** for each item (`kind:"comment"` with `blockId`+`selectedText`, or
   `kind:"answer"` with `questionId`), apply the change. At the CONSENT GATE an `answer` keyed to a
   finding's `questionId` (`f<k>`) carries «Применить»/«Пропустить» — record it as that finding's
   decision in `state.json.findings[]`. A comment's `blockId` is the anchor of the commented region: a
   finding card's id (`f<k>`), a prose-section anchor (`summary`), **or** the demo-variant id
   (`redesign`) — у варианта есть явное поле комментария, так что `comment` может прийти и на него;
   `selectedText` is the exact fragment the human highlighted (для коммента к варианту он обычно пустой).
   Учти также, что `answer` по `questionId` может содержать **свободный текст вне `options`** (человек
   ответил своей формулировкой) — прими его как ответ, не отбраковывай. Then append a reply to
   `replies.json` keyed by the same `blockId`/`questionId` with a one- to two-sentence note on what you
   did, written in the **same language as the comment/answer you are replying to** (auto-detect; this is
   a human-facing reply channel, so the message language overrides the global default). Update
   `lastSubmission`, bump `iteration`, rewrite `dashboard.json` (status back to `working`, then to
   `awaiting-batch` for the next round).

`replies.json` shape:

```json
{ "replies": [
  { "blockId": "f1", "text": "Усилил контраст CTA и состояние нажатия — обновил находку 1.", "ts": "..." },
  { "blockId": "redesign", "text": "Учёл правку макета: подвинул легенду, перепозиционировал бейдж 3.", "ts": "..." },
  { "questionId": "f2", "text": "Принято: находку 2 пропускаем.", "ts": "..." }
] }
```

## Chat — free-form steering at checkpoints

Alongside the batched finding comments, the dashboard has a **chat panel** for free-form steering that
isn't tied to a finding — questions, nudges, scope changes, the screenshot attachment. It coexists with
batches; it does **not** interrupt running coders. Handle it at checkpoints, the same cadence as
everything else.

- Storage: `.workflow/tasks/<slug>/chat.jsonl`, append-only, one JSON object per line
  `{ "role":"human"|"agent", "text":"...", "ts":"...", "phase":"..." }` (a human message may also carry
  `images:[…]` — the screenshot attachment). A human message also raises a `chat` signal, so your parked
  `/wait` returns immediately.
- On wake (or at any IMPLEMENT checkpoint between work-streams), read messages with `ts >
  state.json.lastChatTs`. For each: answer it by **appending your own `role:"agent"` line** to
  `chat.jsonl` (the panel renders it), and if it asks for a change, fold it into the remaining
  work-streams / findings just like a steering batch. Then set `lastChatTs` to the newest message ts.
- Keep replies short, and reply in the **same language as the human's chat message** (auto-detect from
  that message text; the chat is a human-facing reply channel, so the message language overrides the
  global default). If a request is large enough to reshape the audit/plan, say so in chat and reflect it
  in `dashboard.json` rather than silently diverging.
- In headless/eval mode there is no chat; skip it.

### Anchored discussion (ветки на находках)

A `chat.jsonl` message may carry an **`anchor`** — a `planBlocks[].id` (`f<k>`), the literal `summary`,
or the demo-variant id (`redesign`) — plus an optional **`quote`** (the exact fragment the human
selected). Such a message renders as a **threaded discussion under that finding/region/variant** instead
of in the free-form chat panel. This is the per-finding discussion channel.

- **Reply in context** by appending your own `chat.jsonl` line with the **same `anchor`** — your reply
  lands in that thread:

  ```json
  {"role":"agent","text":"Усилил контраст CTA до AA.","anchor":"f1","ts":"<iso>"}
  ```

- **If your reply is a question back to the human**, add **`"needsAnswer": true`**. The page marks that
  finding as having an open thread and raises a header counter «🔸 N ждут ответа», which stays up until
  the human posts a later turn on that anchor. Omit `needsAnswer` for a plain reply — the page then shows
  «✓ учтено агентом».
- `needsAnswer` is **agent-only**: the human's POST never sets it (it only appears on a `role:"agent"`
  line). So a finding is "ждёт ответа" exactly when your latest anchored turn asked something and the
  human hasn't answered yet.

## Don't busy-wait

The `/wait` long-poll is already the no-busy-wait path: you block on a background curl and are
re-invoked only when there's a real event, so you burn zero turns while parked and pick up clicks
near-instantly. Do **not** add a short-interval `ScheduleWakeup` to poll on top of it — keep only the
long (~1850s) fallback. If you are waiting on a long background coder instead of the human, you will be
re-invoked when it finishes — schedule only a long fallback in that case too.

## Eval / headless mode

With `--eval` / `AIPF_EVAL=1`: do not park for the human. If the fixture pre-seeded `submissions/*.json`,
apply them in order; otherwise take all findings as «Применить». Then treat the finding set as approved
and continue. This is what lets the workflow run unattended for benchmarking.
