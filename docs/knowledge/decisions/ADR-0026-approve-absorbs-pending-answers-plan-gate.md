# ADR-0026 — «Approve» absorbs the pending answers (no forced revision round-trip)

_Status: accepted · 2026-07-01_

## Context

At the plan gate the dashboard had a two-button action bar: **«Отправить агенту на
доработку»** (`POST /submit`) and **«Утвердить план»** (`POST /signal approve-plan`).
Answering a question / picking a variant is stored as a `draft.json` item
(`kind:"answer"`, via `saveAnswer → POST /draft`) that accumulates locally until sent.
`updateApproveGate()` **disabled** «Утвердить» while the draft was non-empty:

```js
const blocked = draftItems.length > 0 || (improveGate && !submittedOnce);
```

Because the only control that clears the draft is «Отправить агенту на доработку», the
human who simply answered the open questions was forced through
`Submit → wait for a full revision round → Approve`. The orchestrator, on a submission,
applies the answers, writes replies, bumps the iteration and **re-parks** at
`awaiting-batch` — so the answers (which are *inputs* to the build) triggered a wasted
revision cycle before implementation could start. Free-form comments were never the
problem: they go through the anchored-chat channel (`postAnchored → /chat`) and are
delivered immediately; only unsent **answers** blocked Approve.

## Decision

**«Утвердить план» absorbs the pending draft itself and advances at once.** The gate is
no longer disabled by unsent edits.

Frontend (`templates/dashboard.html`, zero server changes):

- `flushDraft()` — one helper that `POST /submit`s the accumulated answers and clears the
  local draft (snapshotting the ranked picks into `dispatchPreview`). Reused by both
  «Отправить» and «Утвердить».
- **`/feature` plan gate:** on «Утвердить», if the draft is only clean picks (each answer
  equals one of its options) and there is no open discussion thread → `flushDraft()` then
  `approve-plan`, in **one click**. If the human left a **free-form answer** (text differs
  from every option), a comment item, **or an open anchored thread** (agent asked,
  human hasn't answered) → show an inline **ask row**: «Применить и в бой» (flush + approve)
  vs «Сначала на доработку» (flush only). Ask-each-time, per the human's choice at design.
- **`/improve` SELECT gate:** unchanged intent — the two-click **armed** confirm before the
  irreversible dispatch stays. The first click now `flushDraft()`s the picks and arms with
  the count; the second dispatches. The `submittedOnce` session-latch is gone (auto-satisfied).
- Removed: the `submittedOnce` latch, the `①/②` stepper numbering, and the `toast.submitFirst`
  nudge.

Orchestrator (skill prose only): «Утвердить» auto-submits, so a single `/wait` return often
carries **both** a fresh `submissions/<n>.json` and an `approve-plan` signal. Apply the
submission first (record answers, fold picked variants into `plan.md`), **then advance
straight to IMPLEMENT/DISPATCH — do not re-park** at `awaiting-batch`. Pinned in
`skills/feature/phases.md` §4, `skills/feature/feedback-loop.md`, and mirrored for
`/improve` in `dashboard-guide.md`/`feedback-loop.md`/`consensus.md`.

## Consequences

- The common case — answer the questions / pick the variants, then start — is **one click**;
  no phantom revision cycle, no repeated Approve presses.
- Substantive un-actioned feedback (a written correction or an open thread) still gets a
  deliberate confirm, so approving doesn't silently swallow a change the human wanted applied.
- **Zero `server.py` edits** — rides the existing `/submit` + `/signal` contract, only the
  call order on the client changed (lineage ADR-0008/0013/0016 "ride the contract").
- The improve gate keeps its irreversible-dispatch safety (armed two-click + `dispatchPreview`).
- Contract pinned by `tests/test_plan_gate_approve.py` (dashboard JS surfaces + orchestrator
  prose); the removed `toast.submitFirst` key keeps STR en/ru parity (test_settings.py).

Related: ADR-0008 (answer/comment channels, agnostic backend), ADR-0013 (improve `feat-K`
pick contract, Submit→Approve order it supersedes), ADR-0016 (ride-the-contract), ADR-0023
(design-command opt-out consent gate — sibling "default forward" gate policy).
