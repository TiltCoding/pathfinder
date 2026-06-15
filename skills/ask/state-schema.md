# `state.json` — resumable workflow state

One file per task at `.workflow/tasks/<slug>/state.json`. You read it at the start of every `/ask`
invocation to **resume** exactly where you left off, and you rewrite it whenever something meaningful
changes (phase transition, a consolidated research round, a follow-up answered). The base shape below is
shared with `/feature`; the ask-specific fields are additive (see the last section) — a `/feature` run
never has to carry them.

```json
{
  "slug": "ask-dashboard-answer-flow",
  "title": "Как ответ агента попадает на дашборд?",
  "phase": "ANSWER",
  "checkpoint": "awaiting-batch",
  "iteration": 0,
  "createdAt": "2026-06-15T10:00:00",
  "updatedAt": "2026-06-15T10:40:00",
  "baseCommit": "8e53e98eeeae88a5e6b4c85e857340e83264ea2c",
  "serverPort": 8473,
  "lastSignalCount": 1,
  "kind": "ask",
  "questionLog": [
    { "q": "Как ответ агента попадает на дашборд?", "answeredAt": "2026-06-15T10:30:00", "researchFile": "research/1.md" }
  ],
  "lastChatTs": "2026-06-15T10:35:00"
}
```

The ask-specific fields (`kind`, `questionLog`, `lastChatTs`) extend this — see the section below.

Field notes (base):

- **`phase`**: one of `INTAKE / RESEARCH / SYNTHESIZE / ANSWER / DONE`. There is no plan gate, no
  IMPLEMENT, no VERIFY — this is read-only Q&A.
- **`checkpoint`**: `working` while you research/synthesize, `awaiting-batch` while parked on the chat in
  the `ANSWER` stage. Together with `phase` they tell a resumed session what to do next.
- **`iteration`**: optional bookkeeping; `/ask` mostly tracks progress through `questionLog` instead.
- **`baseCommit`**: the git `HEAD` captured at INTAKE. The companion server diffs the working tree
  against it for the **«Изменения»** tab — `/ask` edits no code, so its own «Изменения» tab is usually
  empty. Absent in non-git projects (the server falls back to `HEAD`).
- **`serverPort`**: the companion server port (mirrors `.workflow/server.json`).
- **`lastSignalCount`**: how many `signals.json` entries you have already accounted for. Serves as the
  `/wait` long-poll baseline (`sinceSignal`) in the `ANSWER` chat loop and keeps you from re-processing
  old signals.

On resume: load this file. If `phase === "ANSWER"`, re-check `chat.jsonl` for messages newer than
`lastChatTs`, then re-park on the chat (see `feedback-loop.md`). Otherwise continue the current `phase`
from where `questionLog`/`research/` indicate.

## ask-specific fields (additive)

`/ask` carries a few extra fields on the same `state.json`. They are all **append/extend-only**
(invariant "add, don't rewrite" — `conventions.md`); a `/feature` task simply never has them, so the
two scenarios stay compatible. Write each one as the matching stage produces it.

```json
{
  "phase": "ANSWER",
  "kind": "ask",
  "questionLog": [
    { "q": "Как ответ агента попадает на дашборд?", "answeredAt": "2026-06-15T10:30:00", "researchFile": "research/1.md" },
    { "q": "А где это рендерится на фронте?", "answeredAt": "2026-06-15T10:50:00", "researchFile": "research/2.md" }
  ],
  "lastChatTs": "2026-06-15T10:50:00"
}
```

- **`kind`** — `"ask"`, written at INTAKE. Marks the task type so the hub can badge it as an «ask» run
  (distinct from `/feature`/`/improve`). It is an **optional** field on `state.json`: tasks without
  `kind` (`/feature`, `/improve`) stay fully compatible.
- **`questionLog`** — append-only list of the questions answered in this task, the initial one plus every
  substantive follow-up from the chat loop. Each entry is `{ q, answeredAt, researchFile }` where
  `researchFile` points at the `research/<n>.md` digest that backed the answer. Lets a resumed session
  know what has already been researched and answered.
- **`lastChatTs`** — timestamp of the last `chat.jsonl` message you have already read/answered. On each
  `ANSWER`-stage wake-up you handle messages newer than this, then advance it (see `feedback-loop.md`).

## Related files (not part of `state.json`)

- **`.workflow/active.json`** — `{ slug, updatedAt }`, rewritten on every start/resume. Lets the
  telemetry hooks map a Claude Code session to the active task for session-level events.
- **`.workflow/tasks/<slug>/research/<n>.md`** — the per-facet digests written by `ask-researcher`
  (Russian, the fixed schema), consolidated by you at RESEARCH/ANSWER. Referenced by `questionLog`.
- **`.workflow/tasks/<slug>/mockups/`** — the self-contained `infographic.html` and `process.svg` you
  draw at SYNTHESIZE, served read-only by `GET /mockup`. Names must match `MOCKUP_RE`.
- **`.workflow/tasks/<slug>/chat.jsonl`** — append-only chat thread `{ role, text, ts, phase }`; the
  primary steering channel for `/ask` (see `feedback-loop.md`).
- **`.workflow/tasks/<slug>/telemetry.jsonl`** — append-only event log written by the hooks (and any
  `POST /telemetry` markers): one line per `session.start|session.end|turn.stop|subagent.start|
  subagent.end|file.touch`. The **trace id is the slug**, so a task is one Langfuse trace across
  sessions. `telemetry.cursor` and `telemetry.enriched.json` track forwarding state. All are gitignored
  under `.workflow/`; you don't edit them by hand.
