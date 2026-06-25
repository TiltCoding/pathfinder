# `state.json` — resumable workflow state

One file per task at `.workflow/tasks/<slug>/state.json`. You read it at the start of every `/design`
invocation to **resume** exactly where you left off, and you rewrite it whenever something meaningful
changes (phase transition, new iteration, finding decision, work-stream status, a consumed submission).

```json
{
  "slug": "design-profile-form",
  "title": "Аудит UI/UX: форма настроек профиля",
  "phase": "IMPLEMENT",
  "iteration": 1,
  "checkpoint": "working",
  "createdAt": "2026-06-25T22:00:00",
  "updatedAt": "2026-06-25T22:40:00",
  "component": {
    "name": "форма настроек профиля",
    "location": "src/ui/ProfileForm.tsx:1",
    "screenshot": "attachments/profile-form.png"
  },
  "findings": [
    { "id": "f1", "prism": ["visual-hierarchy", "a11y"], "severity": "high",
      "location": "src/ui/ProfileForm.tsx:42", "decision": "apply" },
    { "id": "f2", "prism": ["copy"], "severity": "low",
      "location": "src/ui/ProfileForm.tsx:18", "decision": "skip" }
  ],
  "questions": [
    { "id": "f1", "text": "Находка 1 — усилить контраст CTA. Применить?", "answer": "Применить" }
  ],
  "workstreams": [
    { "id": "ws1", "title": "Контраст CTA", "finding": "f1", "status": "done" },
    { "id": "ws2", "title": "Фидбэк валидации email", "finding": "f3", "status": "in_progress" }
  ],
  "subagents": [
    { "type": "ds-coder", "workstream": "ws2", "bg": true, "startedAt": "..." }
  ],
  "baseCommit": "8e53e98eeeae88a5e6b4c85e857340e83264ea2c",
  "lastSubmission": 1,
  "lastSignalCount": 1,
  "lastChatTs": "2026-06-25T22:35:00",
  "lang": "en",
  "serverPort": 8473
}
```

Field notes:

- **`phase`** / **`checkpoint`**: `checkpoint` is `working` while you act and `awaiting-batch` while
  parked waiting for a human batch at the CONSENT GATE. Together with `phase` they tell a resumed session
  what to do next. `phase` is one of INTAKE / AUDIT / COMPOSE / CONSENT GATE / IMPLEMENT / VERIFY / DONE.
- **`component`**: the one element under audit — `name` (as the human gave it), `location` (`path:line`
  of its source) and, when given by screenshot, `screenshot` (the **relative** path under
  `.workflow/tasks/<slug>/` of the attached image you `Read`; ADR-0020). Any of `name`/`screenshot` may
  be absent (only-name or only-screenshot intake); `location` is filled once you've located the code.
- **`findings`**: the consolidated, ranked audit output — one entry per finding with a stable `id`
  (`f<k>`), its `prism` tag(s) (merged prisms keep both), `severity`, `location`, and the human's
  **`decision`** (`"apply"` | `"skip"`, set at the CONSENT GATE; default `"apply"` until the human
  unchecks it). This is your record of what the audit found and what was approved; the full prose
  (problem/proposal) lives in `findings.md` and the dashboard card.
- **`questions`**: keep ids stable (each equals a finding's `f<k>`) and store the resolved `answer`
  («Применить»/«Пропустить» or free text) once known. Mirror the apply/skip into `findings[].decision`.
- **`workstreams[].status`**: `todo` | `in_progress` | `done`. One per approved finding (or a grouped
  stream of related findings), with `finding` pointing at the `f<k>` it implements. The source of truth
  for IMPLEMENT progress; mirror it into `dashboard.json.progress` and `workstreams`. Also reflect the
  **current activity** into `dashboard.json.now`/`nowAt` whenever it changes — see `dashboard-guide.md`.
- **`lastSubmission`**: the highest `submissions/<n>` you have already consumed. Compare against
  `submit.flag.latest` to detect a new batch.
- **`lastSignalCount`**: how many `signals.json` entries you have already accounted for. Serves as the
  `/wait` long-poll baseline (`sinceSignal`) and keeps you from re-processing old signals.
- **`baseCommit`**: the git `HEAD` captured at INTAKE. The companion server diffs the working tree
  against it to populate the **Changes** tab (`/changes`). Absent in non-git projects — the server then
  falls back to `HEAD`.
- **`lang`**: the resolved global output language (`"en"` | `"ru"`), read from
  `~/.claude/ai-pathfinder/settings.json` at INTAKE (graceful → `"en"`). The **default** language for
  generated artifacts/dashboard; chat (`chat.jsonl`) and `replies.json` instead follow the language of
  the human's message. Pass it to sub-agents in their spawn prompt.
- **`worktreePath`**: the absolute path of the task's own git working tree (set for every task — see the
  SKILL start procedure). The server diffs the **Changes** tab against this tree instead of the project
  root. Written by `scripts/worktree.py` (append-only); read by the server. Absent only outside a git
  repo (or for old tasks) — the server then falls back to its `--root` working tree.
- **`branch`**: the git branch the task's worktree is checked out on (`<slug>` by default). Set alongside
  `worktreePath` by `scripts/worktree.py`. Absent only outside a git repo or for old tasks.
- **`lastChatTs`**: timestamp of the last `chat.jsonl` message you have already read/answered (also where
  a screenshot attachment arrives). On each checkpoint wake-up you reply to messages newer than this,
  then advance it (see `feedback-loop.md`).
- **`subagents`**: lightweight record of what you spawned (especially background coders) so a resumed
  session knows what is in flight.

On resume: load this file; if `checkpoint === "awaiting-batch"`, re-check `submit.flag`/`signals.json`
before doing anything else; otherwise continue the current `phase` from where `workstreams`/`findings`
indicate.

## Related files (not part of `state.json`)

- **`.workflow/active.json`** — `{ slug, updatedAt }`, rewritten on every start/resume. Lets the
  telemetry hooks map a Claude Code session to the active task for session-level events.
- **`.workflow/tasks/<slug>/findings.md`** — the full prose of the consolidated audit (per-finding
  problem/proposal/location), written at AUDIT consolidation; `state.json.findings[]` is its index.
- **`.workflow/tasks/<slug>/mockups/redesign.html`** — the single self-contained annotated demo built at
  COMPOSE (Variant A: badges + legend + «До/После»). Served read-only by `GET /mockup`.
- **`.workflow/tasks/<slug>/attachments/<file>`** — screenshots the human attaches at INTAKE (ADR-0020).
  You `Read` them by absolute path and pass that path to the auditors.
- **`.workflow/tasks/<slug>/telemetry.jsonl`** — append-only event log written by the hooks (and any
  `POST /telemetry` markers): one line per `session.start|session.end|turn.stop|subagent.start|
  subagent.end|file.touch`. The **trace id is the slug**, so a task is one Langfuse trace across
  sessions. All are gitignored under `.workflow/`; you don't edit them by hand.
