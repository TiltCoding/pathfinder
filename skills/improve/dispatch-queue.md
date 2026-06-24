# Dispatch queue — sequential `/feature` drain (the DISPATCH contract)

`/improve` no longer fans the picked features out into parallel git worktrees. Instead it **queues**
them and they are **drained one at a time through `/feature`**, each in a **fresh context**. This file
is the canonical contract for that queue — both `/improve` (writer) and `/feature` (drainer) follow it.

Why sequential-with-reset instead of parallel worktrees: launching N independent Claude Code sessions
by hand is the friction we are removing, and one session doing all N features accumulates context until
quality degrades. A queue file + one `/feature` per **cleared** session gives full `/feature` quality
(exploration, plan gate, review gates, knowledge growth) on every item while keeping each run's context
clean. (Parallel worktrees still exist as an opt-in — see `parallel.md` — but they are no longer the
default dispatch path.)

## The queue file

One project-level file at **`.workflow/dispatch-queue.json`** (not per-task, so `/feature` finds it
without knowing the `/improve` slug). It is the single durable source of truth for the drain — it
survives the `/clear` between features. Append/extend-only in spirit; statuses move forward only.

```json
{
  "version": 1,
  "source": "improve-runtime",
  "mode": "sequential-feature",
  "autonomous": true,
  "createdAt": "2026-06-13T20:30:00",
  "updatedAt": "2026-06-13T20:30:00",
  "baseCommit": "dd01a10…",
  "items": [
    {
      "n": 1,
      "featId": "feat-1",
      "slug": "server-identity-health",
      "title": "Identity сервера: версия и путь в /health, --version",
      "candId": "cand-24",
      "prism": "DX",
      "briefPath": ".workflow/tasks/server-identity-health/brief.md",
      "status": "pending",
      "startedAt": null,
      "doneAt": null
    }
  ]
}
```

- **`items[]`** — the picked features in **ranked order** (`feat-1` first). `n` is the 1-based queue
  position; `slug` is the `/feature` task slug; `briefPath` points at the brief `/improve` already
  wrote. The `/feature` run keys its own `.workflow/tasks/<slug>/` workspace off `slug`.
- **`status`** per item: `pending` → `in-progress` → `done` (or `skipped` / `failed`). The drain always
  takes the lowest-`n` item still `pending`.
- **`baseCommit`** — the `git HEAD` captured at `/improve` INTAKE. `/feature`, when it starts a queue
  item on the default branch, branches from here so the features stay independent and reviewable
  (the human merges/cherry-picks afterward). If the human prefers a stack, they say so and `/feature`
  branches from the current HEAD instead.
- **`autonomous`** *(optional, top-level)* — when `true`, the **whole queue drains autonomously**: each
  item runs to DONE without parking at the per-feature PLAN GATE, and the agent self-resolves the plan's
  open questions instead of asking the human (subject to the escalation valve below). Absent or `false`
  ⇒ today's manual behavior (every item parks at its PLAN GATE). This is a queue-level flag in v1;
  per-item autonomy is a possible future extension, not part of v1. See **Autonomous drain (opt-in)**.

## Writer side — what `/improve` does at DISPATCH

Per picked feature (in ranked `feat-K` order), `/improve`:

1. Mints a unique kebab-case `<slug>` from the feature title.
2. Writes the tailored **`brief.md`** to `.workflow/tasks/<slug>/` (from `templates/artifacts/brief.md`,
   filled from the candidate **plus** the human's free-form `answer.text` for that `feat-K` if any).
3. Appends an `items[]` entry (status `pending`) to `.workflow/dispatch-queue.json`.

It does **not** create a worktree, and does **not** seed `state.json`/`dashboard.json`/`index.html`
for the feature — the `/feature` run creates its own workspace when it picks the item up. `/improve`
records the same set in its own `state.json.dispatched[]` (`{slug, featId, candId, briefPath, status}`)
and then **hands the drain to the human** (it does not run `/feature` inside its own session — that
would pollute context, defeating the fresh-context goal).

If the human chose **autonomous drain** at the `/improve` SELECT GATE, `/improve` stamps top-level
`autonomous:true` onto the queue at DISPATCH (the mechanics live in `/improve`'s own phases; this file
just records the contract that the drainer reads back).

## Drainer side — what `/feature` does in queue mode

`/feature`, when invoked with **no explicit task** and a `.workflow/dispatch-queue.json` with at least
one `pending` item exists, enters **queue mode**:

1. Pick the lowest-`n` `pending` item; set it `in-progress` (+`startedAt`) and bump the queue's
   `updatedAt`.
2. Adopt its `slug` as the active task and its `briefPath` as the **given** brief — so it **skips
   INTAKE elicitation** and goes straight to EXPLORE (if a `state.json` for that slug already exists,
   resume that instead). On the default branch, branch from the queue's `baseCommit`.
3. Run the normal `/feature` workflow for that one feature: EXPLORE → ELABORATE → PLAN GATE →
   IMPLEMENT → VERIFY → DONE, with its own dashboard, plan gate, and review gates.
4. **At DONE:** mark the item `done` (+`doneAt`) in the queue. Then tell the human plainly: this
   feature is complete; **`/clear` and run `/feature`** to start the next pending item (report how many
   remain), or use **`/loop /feature`** to auto-continue. **Do not** start the next item in the same
   session — a fresh context per feature is the whole point.

When the last item flips to `done`, `/feature` reports the queue is drained and points at `/hub`
(every drained run shows up there as it completes).

## The two drive options (state them to the human)

- **Clean context (recommended):** after each feature finishes, **`/clear`** then **`/feature`**. Each
  feature gets a genuinely fresh context; the queue file carries the state across the clear.
- **Hands-off:** **`/loop /feature`** — the loop re-invokes `/feature`, which pops the next pending item
  each time. Context is compacted by the harness between iterations rather than fully cleared; use this
  when you want to walk away.

## Autonomous drain (opt-in)

This is the **canonical spec** for the opt-in autonomous mode. It is **off by default** — absent
`autonomous` on the queue means every item parks at its PLAN GATE exactly as today.

**What the flag does.** When `/feature` drains a queue whose top-level `autonomous` is `true` (or when
`/feature` is invoked with `--auto` / `--autonomous`, or an equivalent natural-language request, as an
override for that one invocation), it runs each item to DONE **without parking at the PLAN GATE**.
Instead of asking the human, it **auto-resolves the plan's open questions itself** and **auto-approves
the plan**, then proceeds to IMPLEMENT. This lets the human start the drain and walk away.

**Auto-resolve policy.** For each open question, pick the **sensible default** — the option that best
matches, in priority order: (a) existing project conventions and the knowledge base (`docs/knowledge/`),
(b) the **lowest-risk / most reversible** choice, (c) the **smallest scope** that still meets the
brief's acceptance criteria. Record the choice and a short rationale (see Decision trail below).

**Two-tier escalation valve (IMPORTANT).** Autonomy is **not** recklessness. Auto-resolution runs
through a two-tier valve:

- **Soft escalation** — for ordinary ambiguity and reversible decisions: do **not** block. Take the
  sensible default, tag the decision `mode:"escalated"`, record the rationale, surface it in chat and
  in the DONE summary, and continue.
- **Hard block = mandatory human approval** — if a decision is **irreversible / risks data loss /
  destructive**, the agent does **not** do it autonomously. This covers: deleting or overwriting data
  or files, a destructive migration, `DROP` of a table or column, rewriting git history, a breaking
  change to a public / served contract, bulk operations — in short, **anything not revertible via
  git**. In that case the agent **stops that slice**, tags the decision `mode:"blocked"`, raises the
  question to the human (an entry in `state.json.questions[]` **plus** an anchored `chat.jsonl` agent
  line with `needsAnswer:true`), **parks on `/wait` and waits for explicit human approval**, and only
  proceeds after the human answers. This conditional gate fires **even during a fully hands-off
  `/loop /feature` drain**: autonomy ≠ recklessness.

**Decision trail (no new artifacts).** Every auto-decision is recorded in `state.json.questions[]` as
an `answer` plus a short `rationale` plus `mode:"auto"|"escalated"|"blocked"`, and mirrored to
`replies.json` / `chat.jsonl` so it shows on the task dashboard. Genuinely notable decisions also
become an ADR (`docs/knowledge/decisions/`) and a `task-log.md` line. The **DONE summary lists all
auto-decisions**, with `blocked` items and escalations called out in a separate section.

**Predicate separation from eval.** `autonomous` is **independent** of `AIPF_EVAL` — they are different
predicates, not the same switch. On the **PLAN GATE** an autonomous run behaves *like* eval (no park,
auto-approve). On **VERIFY**, however, it behaves like a **normal** run: the `/code-review` and
`/security-review` gates **still run**, high-severity findings are fix-or-justify, and chat steering
stays on. This is the key contrast with eval, which skips the review gates outright.

**Hands-off driving.** The autonomous drain pairs with **`/loop /feature`**. The "fresh context per
feature" invariant still holds: exactly **one `/feature` re-invocation per item** — never chain items
in one session.

## Eval / headless mode

With `--eval` / `AIPF_EVAL=1`, `/improve` writes the queue exactly the same way; the harness (or a test
driver) then runs `/feature` repeatedly to drain it unattended. No worktrees are created.
