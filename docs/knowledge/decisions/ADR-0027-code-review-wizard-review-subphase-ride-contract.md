# ADR-0027 — Code-review wizard as a REVIEW sub-phase, ride-the-contract (0 server changes)

_Status: accepted · 2026-07-05 · Task: code-review-wizard_

## Context

`/feature` ended at VERIFY: `wf-reviewer` ran the tests/linters, then the `/code-review` +
`/security-review` gates ran over the diff and were captured into `reviews.json`. But the human never got
a **guided, ranked walk through the actual change** before it landed. The **Changes** tab already exposes
the worktree diff (`GET /changes`, `GET /changes?file=`), yet it is a flat file tree with no notion of
*importance*, no per-file/per-hunk "what/why" from the agent, and no place to comment hunk-by-hunk and have
the agent fix + reply in a loop. The plan gate has exactly that anchored-thread machinery
(`postAnchored → /chat`, `regionFooter`, `openThreadAnchors`), but only for questions/variants — not for
the diff.

The goal: a step-by-step **code-review wizard** over `/feature`'s own output — files ranked by importance,
hunks inside each file ranked, each annotated with the agent's rationale, driven live by the human, with
comments looping back to the agent — **without** growing the server surface.

## Decision

Add a **REVIEW sub-phase** between VERIFY and DONE (non-terminal, task stays active in the hub), and build
the whole wizard **on the existing contract** — **0 edits to `server.py`**.

- **Model in `dashboard.json.review`** (a new *feature-specific* field; the shared
  `_shared/dashboard-contract.md` is untouched):
  `{ summary, status:"open"|"resolved", iteration, steps:[{ file, anchor:"rev:<path>", status, added,
  removed, rank, kind:"logic"|"cosmetic", comment, blocks:[{ anchor:"rev:<path>#<idx>", hunkHeader,
  range:[start,end], rank, kind, comment }] }] }`. **Array order is the ranking** (rank 1 first). The
  agent ranks files (the wizard step) and hunks inside each file by a **combined-importance heuristic**:
  public contract/API + amount of real logic + risk (security/auth, persistence, parsing) rank **higher**;
  renames, reformats, parameter pass-through, test/fixture tweaks are `kind:"cosmetic"`.

- **Anchor keyed to the hunk index**, `rev:<path>#<idx>`, **not** the line range — ranges drift between
  fix iterations, the index is stable across ticks so a human thread stays attached to the right hunk.

- **Hunk bodies are not duplicated** into the model — the wizard pulls the file's full unified diff from
  the existing `GET /changes?file=<path>` and slices out the matching `@@` hunk client-side
  (`hunkSlice`). One source of truth for the diff, shared with the Changes tab.

- **Human comments ride the anchored-thread channel** (`POST /chat` with a verbatim `anchor`). The agent
  fixes the code and replies on the **same anchor**; the "N awaiting reply" counter is the number of open
  `rev:*` threads. **Closing the review is an `approve-plan` signal** — the "Finish review" button reuses
  the plan-gate approve path (`flushDraft` + `approve-plan`, ADR-0026).

- **A separate "Review" tab** (the 6th tab), not an overlay inside Changes. The entire FE lives in
  `templates/dashboard.html` (`renderReview`/`renderReviewRail`/`renderReviewStep`/`renderReviewStepper`/
  `reviewTick`/`gotoStep`/`hunkSlice`/`kindChip`), with its own `captureReviewInput`/`restoreReviewInput`
  (scoped to `#review`, because the built-in `captureActiveInput` is scoped to `#content`), a signature
  guard carrying the step cursor + collapse + open-thread state through the 5 s poll, and a11y
  (ARIA tabpanel + step announcement in `#phase-announce`). Reuses `renderDiff`/`langFromPath`/
  `regionFooter`/`anchoredThreads`/`openThreadAnchors`/`wireBlocks`.

- **Skill prose** wires the phase: `skills/feature/phases.md` §6.5 REVIEW, "Review wizard cycle" in
  `feedback-loop.md`, the `review` field in `dashboard-guide.md`, the non-terminal `REVIEW` phase in
  `state-schema.md`. Autonomous/eval: publish the same structure but do not park — advance straight to
  DONE and list the ranked steps in the narrative.

## Alternatives considered

- **A new endpoint / `review-plan.json` file** — rejected. It would fork the diff source (the Changes tab
  already serves it) and add server surface for no gain; `dashboard.json` + `/changes` already carry
  everything, and the backend is agnostic to the `review` payload.
- **An overlay inside the Changes tab** — rejected. It would entangle the flat-tree diff UI with the
  ranked, stepped, threaded wizard (two very different interaction models sharing one `#changes` render
  and its `changesTick` signature). A dedicated tab keeps each render/timer/signature independent and
  lets the wizard own its input-preservation scope.
- **Step = hunk** (instead of file + nested blocks) — rejected. A file is the human's unit of review; the
  ranked hunks belong *under* the file so the rail stays short and the "what/why" reads top-down.

## Consequences

- **Zero `server.py` edits** — the wizard rides `/data` (`review` as a new agnostic field), `/changes` +
  `/changes?file=` (hunk bodies), `/chat` with `anchor` (comments), and `approve-plan` (close). Lineage of
  "ride the contract" (ADR-0008/0013/0016/0025/0026).
- The **preview ↔ live parity invariant** (ADR-0024) holds — the entire FE is in the template, mirrored
  into per-task `index.html`.
- The knowledge base and machine keys stay English (ADR-0022); STR `tab.review`/`review.*` are added to
  **both** dictionaries (en/ru) with the smoke-test parity kept.
- Contract pinned by `tests/test_review_wizard.py` (prose + DOM + STR). Plugin version 0.25.0 → 0.26.0.
- **Known debt:** a pre-existing flaky `tests/test_hub.py::HubCardCacheTest` (mtime-cache timing) is
  unrelated to this change and remains.

Related: ADR-0008 (agnostic backend, answer/comment channels), ADR-0013/0016 (ride-the-contract over the
existing plan-gate contract), ADR-0025 (Artifacts tab — sibling "new tab, reuse the confined diff/serve"),
ADR-0026 (`approve-plan` + `flushDraft` reused to finish the review), ADR-0022 (docs/keys stay English),
ADR-0024 (preview↔live parity — all FE in the template).
