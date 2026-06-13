# Consensus & dispatch — the core of CONSENSUS and DISPATCH

This is the heart of `/improve`. It turns a raw swarm of scout candidates into a ranked, deduplicated
top-K shortlist via a **voting panel** + **deterministic aggregation**, and then **seeds** the
human-picked winners as parallel `/feature` runs. You — the orchestrator — drive every step and compute
the ranking deterministically; the LLM agents only **propose** (scout) and **score** (vote). Everything
human-facing (the cards, the gate text, the dispatch instructions) is **Russian**; these instructions
are English.

"Consensus of a swarm" is realized exactly as in the judge panel of `/new-product` (ADR-0006/0007): a
panel of independent scorers + a **deterministic aggregation by the orchestrator**, never "one agent
reaches consensus." Sub-agents cannot spawn sub-agents, so the whole chain — scout fan-out → you
consolidate & dedup → vote fan-out → you aggregate → you dispatch — is mediated by you.

State you read/write lives in `state.json` improve-specific fields (`prisms[]`, `candidates[]`,
`votes[]`, `selected[]`, `dispatched[]`) — see `state-schema.md`.

## 1. Scout fan-out (CONSENSUS input)

You arrive here with `scout/<prism>.md` already written by SCOUT (see `phases.md`). Recap of what each
scout received and returned:

- **What each scout gets:** the audit brief (area + constraints), **its one prism**, and the candidate
  output schema (one block per candidate). Disjoint prisms keep scouts from re-finding the same things —
  but overlap across prisms is expected (two prisms may both flag the same hotspot from different angles),
  which is why the next step dedups.
- **What each scout returns:** a set of candidate blocks `### cand: <title>` with `prism / problem
  (path:line) / change / areas / size (S|M|L) / risk / impact / rationale` (the exact schema lives in
  `agents/wf-improver.md`). You wrote each scout's output verbatim to `scout/<prism>.md`.

## 2. Consolidation & dedup (you)

Merge all `scout/*.md` into one canonical list with **stable** ids and **no duplicates**:

1. **Collect** every candidate from every prism into one pool.
2. **Dedup.** Two candidates are "the same" when they target the **same change** — judge by the
   **affected files/areas** and the **substance of the change**, not the wording. When you merge:
   - keep the clearest title and problem statement,
   - **union the `areas`** (so the merged candidate names every file involved),
   - record which prisms surfaced it (a candidate seen from several prisms is a signal of breadth — keep
     that note; it can break ties later),
   - keep the **larger** `size`/`risk` and the **higher** `impact` when they disagree (conservative).
3. **Assign stable ids** `cand-1 … cand-N` in a deterministic order (e.g. by first prism then title) and
   write a single `candidates.md` — one section per candidate with its `id`, title, prism(s), problem,
   change, areas, size, risk. This file is what the voters see and what you keep in `state.json.candidates[]`
   (`{id, title, prism, problem, change, areas[], size, risk}`).

Keep `cand-*` ids **stable** for the rest of the run — votes and the gate cards key off them.

## 3. Voting panel (3 voters, parallel)

Spawn **3 `wf-improver` agents in vote mode in parallel**. Each one:

- sees the **whole** consolidated list (`candidates.md`, all `cand-1…cand-N`) — never a slice,
- scores **every** candidate independently on the 0–3 scale and returns the vote schema from
  `agents/wf-improver.md`: per `cand-K` → `impact 0–3`, `effort 0–3`, `risk 0–3`, `confidence 0–3`,
  `verdict keep|drop`, `note` (one-line rationale).

Hand each voter the same input (the candidates list + the audit brief); the only thing that differs is
that they are independent panelists. Save each voter's raw output (you'll need all three to aggregate).
This is the "panel of judges" pattern — independent scoring, no agent-to-agent talk; the consensus is
manufactured by **your** aggregation in the next step, not by the voters agreeing.

## 4. Deterministic aggregation (you compute, not an LLM)

For each candidate `cand-K`, combine the three voters' scores into one aggregate record and a single
`score`. Compute it yourself from the numbers — never ask an LLM to "rank them."

```
For each cand-K, over the 3 voters:
  imp  = mean(impact)         # 0–3, higher = better
  eff  = mean(effort)         # 0–3, higher = more costly
  rsk  = mean(risk)           # 0–3, higher = more dangerous
  conf = mean(confidence)     # 0–3, higher = more sure
  keep = (# of keep verdicts) / 3      # agreement: fraction of voters who'd keep it

  raw   = imp − w_e·eff − w_r·rsk      # value minus weighted cost and risk
  score = raw · (conf / 3)             # discount by panel confidence (0..1)
```

- **Default weights:** `w_e = 0.5`, `w_r = 0.5` (effort and risk each shave half a point per unit). They
  are knobs: if the brief says "low-risk wins only," raise `w_r`; if "we have lots of dev time," lower
  `w_e`. State the weights you used in `candidates.md` / the dashboard summary so the ranking is legible.
- **Confidence is a multiplier, not an additive term** — a high-impact idea the panel is unsure about is
  damped, not boosted.
- **Drop the clearly-rejected.** A candidate with `keep == 0` (no voter would keep it) is dropped before
  ranking, regardless of `score`.

Then:

1. **Sort** the remaining candidates by `score` descending.
2. **Tie-break by `keep`** (agreement) — when two `score`s are within a small epsilon, the one more
   voters agreed to keep ranks higher; if still tied, prefer the higher `imp`, then the smaller `eff`.
3. **Take top-K = 6–8** (use 6 when many candidates are weak, 8 when the field is strong and the brief
   wants breadth). These go to the SELECT GATE.

Record per candidate in `state.json.votes[]` the aggregate `{candId, impact, effort, risk, confidence,
keep, score}`, and keep the top-K (mapped to `feat-1…feat-K` for the gate) in order. Show the human a
legible ranking in the dashboard summary (candidate · score · keep · impact/effort/risk) so the gate is
not a black box.

## 5. SELECT GATE (handoff to the human)

The top-K render into the dashboard as feature-pick cards + choice questions under the **`feat-K`
contract** — one `planBlocks[]` card and one `questions[kind:"choice"]` per candidate, **both keyed by
`feat-K`**, with `options:["Делаем","Пропускаем"]`. The full render/contract (defaults, Submit→Approve
order, free-form answers) lives in `dashboard-guide.md` §SELECT GATE; the stage flow is in `phases.md`
§PROPOSE/SELECT GATE. The human's picked `feat-K`s land in `state.json.selected[]` and drive DISPATCH.

## 6. DISPATCH — queue-and-drain (the exact sequence)

For each picked feature (`feat-K` answered «Делаем», or a free-form "делаем…"), you **queue** it for a
sequential `/feature` drain — you do **not** create a worktree and do **not** run `/feature` yourself.
The full contract (queue schema, drainer behaviour, the two drive options) is in `dispatch-queue.md`;
this is the writer-side sequence. Per feature, in ranked `feat-K` order:

1. **Fresh slug.** Mint a unique kebab-case `<slug>` from the feature title. A slug collision means
   reusing someone else's task dir — avoid it; pick a unique slug (e.g. add a discriminator).

2. **Write the brief** to `.workflow/tasks/<slug>/brief.md` — from `templates/artifacts/brief.md`,
   filled with the feature's goal / scope / acceptance from its candidate, **plus** the human's
   free-form `answer.text` if they typed one for this `feat-K` (it refines the brief). This is the only
   artifact you seed — the `/feature` run creates its own `state.json`/`dashboard.json`/`index.html`
   when it picks the item up.

3. **Append a queue item** to `.workflow/dispatch-queue.json` (create the file on the first feature with
   `version/source/mode/createdAt/baseCommit`, where `baseCommit` = the `/improve` INTAKE `baseCommit`):
   `{ n, featId, slug, title, candId, prism, briefPath, status:"pending", startedAt:null, doneAt:null }`.
   Keep items in ranked order (`feat-1` → `n:1`). See `dispatch-queue.md` for the exact shape.

Also append a `dispatched[]` entry to **this** task's `state.json` per feature:
`{slug, featId, candId, briefPath, status:"queued"}` (no `worktreePath` — there is no worktree).

### Then hand the drain to the human (do NOT run `/feature` yourself)

Running `/feature` inside the `/improve` session would pollute this context with the feature's work,
defeating the fresh-context goal. Instead, at DONE you tell the human the two drive options (also in
`dispatch-queue.md` §drive options):

- **Clean context (recommended):** run **`/feature`** to start (it pops queue item 1 and runs the full
  workflow); when it finishes, **`/clear`** then **`/feature`** again for the next pending item.
- **Hands-off:** **`/loop /feature`** — re-invokes `/feature`, which pops the next pending item each
  time; the harness compacts context between iterations.

### What you do NOT do

- **No worktree** (`worktree.py add`), **no per-feature `state.json`/`dashboard.json`/`index.html`** —
  the `/feature` drainer creates its own workspace from the brief + queue item.
- **Do not auto-launch `/feature`** from this session. The drain is a human/`/loop` step.
- Parallel git-worktree fan-out is still available if the human explicitly asks for it (`parallel.md`),
  but it is no longer the default dispatch path.

## 7. Default knobs & eval mode

- **Swarm:** 7 scouts (one per prism). **Panel:** 3 voters. **Top-K:** 6–8.
- **Aggregation defaults:** `w_e = 0.5`, `w_r = 0.5`, confidence as a `conf/3` multiplier, drop
  `keep == 0`, tie-break by `keep` then `imp` then `−eff`.
- **Headless / eval mode** (`AIPF_EVAL=1`): keep the fixed counts (7 scouts, 3 voters); auto-pick the
  top-K (or apply any pre-seeded `submissions/`); auto-approve the gate; write the dispatch queue with
  no human present. This guarantees a finite, unattended run that reaches DISPATCH and queues ≥1 valid
  feature item; a test driver then runs `/feature` repeatedly to drain the queue.
