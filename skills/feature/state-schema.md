# `state.json` — resumable workflow state

One file per task at `.workflow/tasks/<slug>/state.json`. You read it at the start of every `/feature`
invocation to **resume** exactly where you left off, and you rewrite it whenever something meaningful
changes (phase transition, new iteration, work-stream status, a consumed submission).

```json
{
  "slug": "add-csv-export",
  "title": "Добавить экспорт отчётов в CSV",
  "phase": "IMPLEMENT",
  "iteration": 3,
  "checkpoint": "working",
  "createdAt": "2026-06-09T22:00:00",
  "updatedAt": "2026-06-09T22:40:00",
  "questions": [
    { "id": "q1", "text": "Разделитель?", "answer": "запятая" }
  ],
  "answers": [],
  "workstreams": [
    { "id": "ws1", "title": "Сервис экспорта", "status": "done" },
    { "id": "ws2", "title": "Эндпоинт + тест", "status": "in_progress" }
  ],
  "subagents": [
    { "type": "wf-coder", "workstream": "ws2", "bg": true, "startedAt": "..." }
  ],
  "baseCommit": "8e53e98eeeae88a5e6b4c85e857340e83264ea2c",
  "lastSubmission": 2,
  "lastSignalCount": 1,
  "lastChatTs": "2026-06-09T22:35:00",
  "serverPort": 8473
}
```

Field notes:

- **`phase`** / **`checkpoint`**: `checkpoint` is `working` while you act and `awaiting-batch` while
  parked waiting for a human batch. Together with `phase` they tell a resumed session what to do next.
- **`workstreams[].status`**: `todo` | `in_progress` | `done`. The source of truth for IMPLEMENT
  progress; mirror it into `dashboard.json.progress` and `workstreams`.
- **`lastSubmission`**: the highest `submissions/<n>` you have already consumed. Compare against
  `submit.flag.latest` to detect a new batch.
- **`lastSignalCount`**: how many `signals.json` entries you have already accounted for. Serves as the
  `/wait` long-poll baseline (`sinceSignal`) and keeps you from re-processing old signals.
- **`baseCommit`**: the git `HEAD` captured at INTAKE. The companion server diffs the working tree
  against it to populate the **«Изменения»** tab (`/changes`). Absent in non-git projects — the server
  then falls back to `HEAD`.
- **`lastChatTs`**: timestamp of the last `chat.jsonl` message you have already read/answered. On each
  checkpoint wake-up you reply to messages newer than this, then advance it (see `feedback-loop.md`).
- **`subagents`**: lightweight record of what you spawned (especially background coders) so a resumed
  session knows what is in flight.
- **`questions`**: keep ids stable and store the resolved `answer` once known — this is your record of
  decisions (also feed notable ones to the knowledge base as ADRs).

On resume: load this file; if `checkpoint === "awaiting-batch"`, re-check `submit.flag`/`signals.json`
before doing anything else; otherwise continue the current `phase` from where `workstreams` indicate.

## Related files (not part of `state.json`)

- **`.workflow/active.json`** — `{ slug, updatedAt }`, rewritten on every start/resume. Lets the
  telemetry hooks map a Claude Code session to the active task for session-level events.
- **`.workflow/tasks/<slug>/telemetry.jsonl`** — append-only event log written by the hooks (and any
  `POST /telemetry` markers): one line per `session.start|session.end|turn.stop|subagent.start|
  subagent.end|file.touch`. The **trace id is the slug**, so a task is one Langfuse trace across
  sessions. `telemetry.cursor` records how many lines the server has already forwarded, and
  `telemetry.enriched.json` tracks which sub-agent spans have had their token usage back-filled to
  Langfuse from transcripts. All are gitignored under `.workflow/`; you don't edit them by hand.
