# Feedback loop — companion server + the chat-driven ANSWER stage

`/ask` has **no plan gate and no batched-submission checkpoint** — there is nothing to approve. The
human steers entirely through the **chat panel**, and your one parked stage is `ANSWER`, where you wait
for follow-up questions. The human and you communicate through a small local server and the per-task
dashboard.

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
`dashboard-guide.md`). The server persists the human's side: `chat.jsonl` (the chat thread),
`signals.json` (e.g. a `chat` signal), and — though `/ask` doesn't use a gate — `draft.json` /
`submissions/<n>.json` if the human ever highlights a fragment and comments.

## Signals the dashboard can raise

`signals.json` is an append-only log; the dashboard `POST /signal`s to it and wakes your `/wait`. For
`/ask` the signal you care about is:

- **`chat`** — a new human message landed in `chat.jsonl`. This is what drives the `ANSWER` loop below.
  (`/ask` does **not** use `approve-plan`: there is no plan gate, no SELECT GATE, no submission to
  approve. If a stray `approve-plan` ever arrives, ignore it.)

`/ask` has no VERIFY phase and writes no `reviews.json`: it implements no code, so there is no diff to
review.

## The ANSWER loop — chat-driven follow-up questions

After SYNTHESIZE you deliver the first answer and **stay in the `ANSWER` stage** to take follow-up
questions. This is what keeps the task **active** in the hub (a non-terminal `phase`).

1. Set `dashboard.json.status = "awaiting-batch"` and write it. Tell the human in chat, briefly, that
   the answer (text + the two visualizations) is on the dashboard and they can ask follow-ups in the
   chat panel. Record your baseline in `state.json`: `lastSignalCount = len(signals.json.signals)` and
   `lastChatTs` = the ts of the newest message you've already handled (your own first answer).
2. **Park on the long-poll, not a timer.** Read `url` from `.workflow/server.json` and start a
   **background** `curl` (Bash tool, `run_in_background: true`) on the `/wait` endpoint, listening for a
   chat signal:

   ```bash
   curl -sS --max-time 1830 \
     "<url>/wait?slug=<slug>&sinceSignal=<lastSignalCount>&timeout=1800" \
     || true
   ```

   `/wait` blocks until a new signal lands and then returns instantly, so the harness re-invokes you
   **the moment the human sends a chat message** — near-zero latency, no idle wake-ups. As a **fallback
   only**, also set a long `ScheduleWakeup` (~1850s, just past the curl timeout) with the same `/ask`
   prompt, in case the server or curl dies.
   - On wake, **read `chat.jsonl`** and take every `role:"human"` message with `ts > state.json.lastChatTs`.
     Handle each (below), then set `lastChatTs` to the newest message ts and update `lastSignalCount`.
   - If nothing new (a rare spurious return), re-park (repeat steps 1–2).
3. **Handling a follow-up message** — two paths:
   - **A simple clarification** (rephrase, "что значит X", "а это где?") that the consolidated research
     already covers → answer **inline**: append a `role:"agent"` line to `chat.jsonl` (Russian, short).
     No new research, no `demo` change. Bump `dashboard.json.updatedAt` only if you also tweaked the
     answer text.
   - **A substantive new question** (a new angle the digests don't cover) → run a **new mini-swarm**: split
     the new question into facets, spawn `ask-researcher` in parallel (writing `research/<n+1>.md`, the
     next index), **consolidate** their digests as in `phases.md`, then **update** `summary`/`planBlocks`
     and **re-draw** the `demo` (`infographic.html` + `process.svg`) to reflect the fuller answer. Bump
     `updatedAt`; **`phase` stays `ANSWER`**. Append `{q, answeredAt, researchFile}` to
     `state.json.questionLog[]`. Then post a `role:"agent"` chat line summarizing the new answer and
     pointing at the updated visualizations.
   - Keep chat replies short and in Russian. If a follow-up is large enough to reshape the whole answer,
     say so in chat and reflect it in `dashboard.json` rather than silently diverging.
4. **The human asks unlimited follow-ups.** Re-park after each. The loop ends only on:
   - **Auto-DONE after ~24h with no new chat message** — the same window that keeps the task *active* in
     the hub. When you wake on the long fallback and find no new message and the last activity is older
     than ~24h, advance to DONE (see `phases.md` §DONE).
   - **An explicit wrap-up request** from the human ("спасибо, всё", "можно закрывать") — advance to DONE.

### Anchored discussion (ветки на блоках)

A `chat.jsonl` message may carry an **`anchor`** — a `planBlocks[].id` (`ans-K`), the literal `summary`,
or a demo-variant id (`process` / `infographic`) — plus an optional **`quote`** (the exact fragment the
human selected). Such a message renders as a **threaded discussion under that block/region/variant**
instead of in the free-form chat panel. This is now the **per-block discussion channel** — it replaces
the old draft-comment cards, so the human can pin a follow-up to a specific part of the answer.

- **Reply in context** by appending your own `chat.jsonl` line with the **same `anchor`** — your reply
  lands in that thread:

  ```json
  {"role":"agent","text":"Да, поток данных именно через server.py:223.","anchor":"ans-1","ts":"<iso>"}
  ```

- **If your reply is a question back to the human** (e.g. you need them to narrow the follow-up), add
  **`"needsAnswer": true`**. The page marks that block as having an open thread and raises a header
  counter «🔸 N ждут ответа», which stays up until the human posts a later turn on that anchor. Omit
  `needsAnswer` for a plain reply — the page then shows «✓ учтено агентом».
- `needsAnswer` is **agent-only**: the human's POST never sets it (it only appears on a `role:"agent"`
  line). So a block is "ждёт ответа" exactly when your latest anchored turn asked something and the human
  hasn't answered yet.

## Don't busy-wait

The `/wait` long-poll is already the no-busy-wait path: you block on a background curl and are
re-invoked only when there's a real event (a chat message), so you burn zero turns while parked and pick
up the human's questions near-instantly. Do **not** add a short-interval `ScheduleWakeup` to poll on top
of it — keep only the long (~1850s) fallback. If you are waiting on a long background sub-agent (a
researcher in a re-research mini-swarm) instead of the human, you will be re-invoked when it finishes —
schedule only a long fallback in that case too.

## Eval / headless mode

With `--eval` / `AIPF_EVAL=1`: there is **no chat**. Produce the first answer at SYNTHESIZE and advance
**straight to DONE** — do not park in `ANSWER`, do not wait for follow-ups. This is what lets the
workflow run unattended for benchmarking.
