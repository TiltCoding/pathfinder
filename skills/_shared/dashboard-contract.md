# Shared dashboard / feedback contract (canonical)

This is the **single source of truth** for the parts of the companion-server + dashboard contract that
**every** ai-pathfinder workflow (`feature`, `new-product`, `improve`, `ask`, `design`, `docs`, `test`,
`debug`) shares. Each skill ships its own `feedback-loop.md`, `dashboard-guide.md` and `state-schema.md`
that layer **per-skill specifics** on top of this core (its gate semantics, extra `state.json` fields,
which tabs it uses). When the invariant contract below changes, change it **here** — the per-skill files
should defer to this document for the core and only document their own deltas.

These instruction files are English regardless of a run's output language.

## Companion server

- One server per project root: `${CLAUDE_PLUGIN_ROOT}/scripts/server.py` (stdlib only). Start it **in the
  background** from the project root; the launch is **idempotent** — a live server for this root prints
  `reusing live server …` and exits without binding a second port.
- It writes `.workflow/server.json` = `{port, pid, url, root, ts}`; the **port is stable per project
  root** (derived from `sha1(root)`), so the dashboard URL does not drift between runs. A `ts` heartbeat
  lets a reader tell a live server from a corpse; `--gc` reaps stale ones.
- Per task: copy `${CLAUDE_PLUGIN_ROOT}/templates/dashboard.html` → `.workflow/tasks/<slug>/index.html`
  and give the human `http://localhost:<port>/?slug=<slug>`.

## The two stores

- **Per-task scratch** `.workflow/tasks/<slug>/` (gitignored): `state.json`, `dashboard.json`,
  `replies.json`, `chat.jsonl`, `submissions/<n>.json`, `draft.json`, `signals.json`, `submit.flag`,
  `telemetry.jsonl`, plus any artifacts (`mockups/`, `attachments/`, scout/votes scratch, …).
- **Durable knowledge base** `docs/knowledge/` (committed): the flywheel the documenter grows at DONE.
  Always English (eng-first), regardless of the run language.

## dashboard.json — the render model

The page is static and **data-driven**: it polls `GET /data?slug=<slug>` (your `dashboard.json`) and
`GET /replies?slug=<slug>` every few seconds and re-renders. To "update the dashboard" you **rewrite
`dashboard.json`** — never hand-edit the HTML. Write it **atomically** (`_aipf.write_json` →
`atomic_write`, ADR-0021), never a raw truncate-write (parallel `/loop` drains are read-modify-write into
one shared store across sessions).

Core shape (skills add fields):

```json
{
  "slug": "…", "title": "…",
  "phase": "<stage>",            // the skill's current stage machine label
  "status": "working" | "awaiting-batch",
  "iteration": 0,
  "summary": "Markdown shown at the top.",
  "planBlocks": [ { "id": "…", "title": "…", "body": "Markdown" } ],
  "questions":  [ { "id": "…", "text": "…", "kind": "open"|"choice", "options": ["…"] } ],
  "updatedAt": "<iso>"
}
```

- **`status`** is `working` while you act and `awaiting-batch` while parked at a gate — keep it honest;
  it is how the human knows whether a click will be seen. Bump **`updatedAt`** on every write (the page
  uses it, plus phase/status/reply-count, to detect changes).
- **Markdown** is supported in `summary` and block `body`. **Stable ids** on `planBlocks[]`/`questions[]`
  are essential: the human's comments and your replies key off them — reuse ids across iterations, mint a
  new id only for genuinely new content.
- Optional shared fields: `workstreams[]` (`{title,status}` cards), `progress`, `now`/`nowAt` (the live
  «Сейчас: …» line), `demo` (visual previews). The `Трейсинг`/`Изменения`/`Артефакты`/`База знаний`/`Hub`
  tabs are populated by the server on demand — you do not write them.

## The one gate: Submit → Approve (batched feedback)

The human writes comments/answers **any time**; you read them **only at a checkpoint**, in batches. The
mandatory order is **Submit → Approve**:

1. The human picks/comments, then clicks **«Отправить»** (Submit) — the server freezes the batch into
   `submissions/<n>.json` and bumps `submit.flag`. `draft.json` is **not** server-readable, so you cannot
   see the picks until they are submitted.
2. Then **«Утвердить план»** (Approve) raises an **`approve-plan`** signal in `signals.json`. Its meaning
   is per-workflow (approve the plan / dispatch the picks / …) but the button + signal are the same.

Processing a submission: apply every item (`kind:"comment"` with `blockId`+`selectedText`, or
`kind:"answer"` with `questionId`; a free-form `answer.text` outside the options is valid — ADR-0008),
then write a short **`replies.json`** entry per item keyed by the same `blockId`/`questionId`, in the
language of the comment. A **chat panel** (`chat.jsonl`, append-only, `{role,text,ts,phase}` + optional
`anchor`/`quote`) carries free-form steering and per-block threads, consumed at the same checkpoints.

## Parking on `/wait` (no busy-wait)

At a checkpoint set `status:"awaiting-batch"`, record baselines in `state.json`
(`lastSubmission = submit.flag.latest`, `lastSignalCount = len(signals)`), then **block on a background
long-poll**:

```
curl -sS --max-time 1830 \
  "<url>/wait?slug=<slug>&sinceSubmission=<lastSubmission>&sinceSignal=<lastSignalCount>&timeout=1800"
```

`/wait` returns the instant a new submission/signal/chat lands, so you are re-invoked with near-zero
latency and burn no turns while parked. Add only a long (~1850s) `ScheduleWakeup` fallback in case the
server/curl dies — never a short polling timer.

## Endpoints (read unless noted)

`GET /data` · `GET /replies` · `GET /chat` (`&since=<offset>` for the tail) · `GET /changes`
(working tree vs `state.json.baseCommit`) · `GET /trace` · `GET /hub.json` · `GET /queue.json` ·
`GET /mockup` · `GET /artifact` · `GET /settings.json` · `GET /wait` (long-poll).
`POST` (state-changing, behind the CSRF/Host guard): `/submit` · `/signal` · `/draft` · `/draft/remove` ·
`/chat` · `/attach` · `/telemetry` · `/queue/op` · `/settings`. Hot reads (`/data`, `/settings.json`,
`/hub.json`, `/queue.json`) carry a weak **ETag** and answer `If-None-Match` with a bodiless **304**.

## state.json — shared fields (skills add their own)

```json
{
  "slug": "…", "title": "…", "phase": "<stage>", "iteration": 0,
  "checkpoint": "working" | "awaiting-batch",
  "createdAt": "<iso>", "updatedAt": "<iso>",
  "baseCommit": "<git HEAD at INTAKE>",
  "lang": "en" | "ru",
  "lastSubmission": 0, "lastSignalCount": 0, "lastChatTs": "<iso>",
  "questions": [], "answers": [], "subagents": []
}
```

- **`checkpoint`** mirrors `status`; with `phase` it tells a resumed session what to do next. On resume:
  load this file; if `checkpoint == "awaiting-batch"`, re-check `submit.flag`/`signals.json` before
  anything else; otherwise continue the recorded `phase`.
- **`baseCommit`** = `git rev-parse HEAD` at INTAKE — the `Изменения` tab diffs against it.
- **`lang`** — the resolved run language (the human's request language; fallback the global
  `~/.claude/ai-pathfinder/settings.json`, default English). It governs all human-facing output; pass it
  to every sub-agent. `docs/knowledge/**` and git commit messages stay English regardless.
- **`worktreePath`/`branch`** (optional) — set when the task runs in its own git worktree (the default;
  see each skill's `parallel.md`). Absent on read-only audits.

## Telemetry & language (automatic / cross-cutting)

Bundled hooks record a span per session and per sub-agent to `.workflow/tasks/<slug>/telemetry.jsonl`
(trace id = slug); the server forwards to Langfuse when keys are set, local-only otherwise. You just keep
`state.json.phase` and `.workflow/active.json` current. Eval (`AIPF_EVAL=1`) skips the human gate;
**autonomous** drain (queue `autonomous:true` / `--auto`) skips the PLAN-GATE park but **keeps** VERIFY +
the review gates — these two predicates are independent (see the dispatch-queue contract).
