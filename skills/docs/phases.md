# Phases — what to do in each stage (`/docs`)

Each stage ends by updating `state.json` (phase, iteration, checkpoint) and `dashboard.json`. Spawn
sub-agents with the Agent tool; pass them the slug and the absolute workspace path. `/docs` produces
**only documentation** edits (`docs/**`, the README, sparingly a code comment) — never a change to logic.

## 1. INTAKE

Goal: capture the area/scope and stand up the workspace.

- Resolve the **target**: an area (`scripts/server.py` + its area doc), the README, an ADR set, or "audit
  the whole knowledge base for drift". Write `brief.md` (goal: what to document/refresh and why; scope/
  non-scope; any constraint like "README only", "just the line-pointers"). In queue mode the brief is
  given — adopt it, skip elicitation.
- Create `state.json` (`phase:"INTAKE"`, `iteration:0`), record `baseCommit = git rev-parse HEAD`,
  resolve the run language (`lang`), start the server, copy the dashboard, print the URL (see
  `feedback-loop.md`). Advance to AUDIT.

## 2. AUDIT (autonomous)

Goal: find where the docs have drifted from the code.

- Spawn **`ask-researcher`** read-only — one, or several in parallel for a broad scope (one per facet:
  the area doc(s), the README, the relevant ADRs, `INDEX.md` line-pointers). Each compares its slice of
  the docs against the **actual code** and returns a **drift list**: per item `{ doc path:line, claim,
  code path:line, reality, fix }` — a renamed symbol, a removed flag, a stale line-pointer, an
  out-of-date contract, or a genuine gap (code with no doc). Each reads `docs/knowledge/INDEX.md` first.
- Consolidate the researchers' drift lists yourself (dedup). Update `dashboard.json` (a short "auditing
  docs↔code" summary) and advance to COMPOSE.

## 3. COMPOSE (autonomous)

Goal: turn the drift list into a concrete set of doc edits.

- For each confirmed drift (and each gap worth filling), draft the **doc edit**: which file, the exact
  before→after (or the new paragraph), and the `path:line` it corrects. Group related edits per doc.
  Note any open decision for the human (e.g. "the area doc is stale enough to rewrite vs patch?", "add a
  new ADR for this contract change or just a task-log line?").
- Write the proposed edits into `dashboard.json` as `planBlocks[]` (stable ids `doc-1…doc-N`, each card =
  one doc edit with before→after + the drift it fixes) and the open decisions as `questions[]`. Set
  status `awaiting-batch`. Advance to PLAN GATE.

## 4. PLAN GATE (the one human gate)

Goal: the human reviews and approves the doc diff. Batched-feedback loop, exactly like `/feature`.

- **Park** and wait (see `feedback-loop.md`). On a new `submissions/<n>.json`: apply every comment/answer
  (drop an edit, reword, answer an open decision), refine the plan, bump `iteration`, re-park, write a
  short `replies.json` entry per item.
- On **«Утвердить план»** (`approve-plan`): record the approved edits + answers in `state.json` and
  advance to WRITE.
- **Autonomous drain / eval:** skip the park — self-resolve open decisions with sensible defaults
  (existing knowledge-base conventions → smallest faithful edit; record the rationale) and auto-approve.

## 5. WRITE (autonomous)

Goal: write the approved doc edits.

- Spawn **`wf-documenter`** to apply the approved edits to `docs/knowledge/` / README / (sparingly) a
  code comment, following `knowledge-guide.md` (architecture / area docs / ADRs / glossary / task-log)
  and the **eng-first** invariant (docs/README English unless the human asked otherwise). It **refreshes
  `INDEX.md`** (one line per doc, trailer up to date) and matches the existing docs' style/structure — no
  restructuring. Split into work-streams per doc if large; mark each `done` as it lands. It must **not**
  touch production logic. Update `dashboard.json` work-streams/progress.
- The dashboard's **«Изменения»** tab now shows the real doc diff against `baseCommit`. Advance to DONE.

## 6. DONE

- Final `dashboard.json` summary: the docs edited, what drift each fixed, that `INDEX.md` is current.
- Add the `task-log.md` line for this run (the documenter does it as part of WRITE; confirm it landed).
- Set `phase:"DONE"`. In queue mode, mark the queue item `done` (`scripts/queue.py done <slug>`) and tell
  the human the drain continues. Otherwise point the human at the dashboard, the «Изменения» diff, and
  the refreshed knowledge base.
