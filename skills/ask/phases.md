# Phases — what to do in each stage

Each stage ends by updating `state.json` (phase, the ask-specific fields) and `dashboard.json`. Spawn
sub-agents with the Agent tool; pass them the slug, the absolute workspace path, and the **facet/focus**
they cover. Keep the human's dashboard truthful at all times. The `demo` visualization contract lives in
`dashboard-guide.md` and the chat loop in `feedback-loop.md` — this file is the stage map that calls
into them.

## 1. INTAKE

Goal: capture the question and stand up the workspace.

- Write the user's question into `brief.md` (from `templates/artifacts/brief.md`) **as a question, not a
  spec**: the exact question, any scope the user gave (e.g. "только про дашборд"), and what to focus on.
  Ask the user only for a real blocker you cannot infer — a question is light, not a task brief.
- Create `state.json` (see `state-schema.md`) with `phase: "INTAKE"`, `kind: "ask"`. In a git repo,
  record `baseCommit` = `git rev-parse HEAD`.
- Start the companion server and copy the dashboard (see `feedback-loop.md`). Write the first
  `dashboard.json` (`title` = a short phrasing of the question, `status: "working"`, a one-line "ищу
  ответ…" summary) and give the user the URL.
- Advance to RESEARCH.

## 2. RESEARCH (autonomous — a mini-swarm)

Goal: gather the evidence to answer, from every facet the question touches.

- **Split the question into facets** — the disjoint angles it spans, typically a subset of:
  1. **База знаний / доки** — what `docs/knowledge/` already says (always start here).
  2. **Серверный код** — `scripts/server.py`, `scripts/_aipf.py`, hooks (the back-end behaviour).
  3. **Дашборд / фронт** — `templates/dashboard.html` (the client-side render/UX).
  4. **Тесты** — `tests/` (what behaviour is pinned, how to run it).
- **Spawn `ask-researcher` in parallel, one per facet** — usually **2–4**; a narrow question needs only
  **1–2**. Keep the facets disjoint so researchers don't overlap. Each one **reads `INDEX.md` first**,
  then surveys its facet of the code with `path:line` evidence, writes its digest to `research/<n>.md`
  (Russian, the fixed schema from `agents/ask-researcher.md`), and returns a short summary to you.
- **Consolidate the digests yourself** into a single picture: merge the `## Ответ` theses, union the
  `## Опорные источники`, order the `## Шаги рассуждения` into one reasoning path, collect the
  `## Числа/связи` facts, and note any `## Уверенность/пробелы`. This consolidated picture is what you
  synthesize from — researchers don't see each other.
- Update `dashboard.json` (a short "собираю ответ…" summary), set `state.json.phase = "RESEARCH"`,
  record each spawned researcher and its `research/<n>.md` file, advance to SYNTHESIZE.

## 3. SYNTHESIZE (autonomous — you do it yourself)

Goal: turn the consolidated research into a visual answer. **You** write everything here — you are the
only one with Write, and sub-agents cannot spawn sub-agents, so the synthesis never goes to an agent.

- **Write the answer** into `dashboard.json.summary` (Russian markdown): a clear, scannable explanation
  grounded in the `path:line` evidence from the digests. For a long answer, break it into `planBlocks`
  cards (stable ids) — one card per sub-topic — keeping `summary` as the lead.
- **Draw the infographic** → `mockups/infographic.html`: KPIs/numbers/relations from the consolidated
  `## Числа/связи` (key files, the data path, the headline numbers). Self-contained, inline CSS, **dark
  dashboard style**, **no CDN**.
- **Draw the process diagram** → `mockups/process.svg`: how the answer was reached — read `INDEX`/docs →
  found the files/lines → reasoning steps → answer — built from the consolidated `## Шаги рассуждения`
  and `## Опорные источники`. **Static** (drawn by you from the digests, not from the live trace).
- **Assemble the `demo`** with both files as variants (see `dashboard-guide.md` §the two visualizations
  for the exact `demo` shape).
- Rewrite `dashboard.json` (the answer + the `demo`), set `state.json.phase = "SYNTHESIZE"`, advance to
  ANSWER.

## 4. ANSWER (non-terminal — the chat loop)

Goal: deliver the answer and stay available for follow-up questions. This is the stage that keeps the
task **active** in the hub.

- Set `state.json.phase = "ANSWER"`, `dashboard.json.status = "awaiting-batch"`. Tell the human in chat
  that the answer is on the dashboard and they can ask follow-ups in the chat panel.
- **Park on the long-poll `/wait`** (`sinceSignal`), listening for the `chat` signal — do **not**
  busy-wait. On wake, read `chat.jsonl` messages newer than `state.json.lastChatTs` and handle them (the
  full loop is in `feedback-loop.md`):
  - **A simple clarification** → answer inline by appending a `role:"agent"` line to `chat.jsonl`.
  - **A substantive new question** → run a **new mini-swarm** of `ask-researcher` (writing
    `research/<n+1>.md`), consolidate, **update** `summary`/`planBlocks` and **re-draw** the `demo`, then
    answer in chat. Bump `updatedAt`; `phase` stays `ANSWER`. Append `{q, answeredAt, researchFile}` to
    `questionLog[]` and advance `lastChatTs`.
  - The human may ask **unlimited** follow-ups; re-park after each.
- **Auto-advance to DONE after ~24h with no new chat message** (the same window that keeps the task
  *active* in the hub), or immediately on the human's explicit request to wrap up.
- In headless/eval mode there is no chat: after the first answer, advance straight to DONE.

## 5. DONE

- Set `state.json.phase = "DONE"` (this moves the task into the hub's «История»). Write a final
  `dashboard.json` (the answer stands, `status` reflecting completion) and tell the human the Q&A is
  wrapped up.
- **Optionally** spawn `wf-documenter` to grow `docs/knowledge/` (see `knowledge-guide.md`) if the
  research surfaced something durable worth recording — a task-log entry, or an area-doc note. For a
  routine question this step can be skipped; it is a peer step when the answer taught the project
  something new.
