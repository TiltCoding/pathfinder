# Knowledge base guide — the product's `docs/knowledge/`

For a greenfield product the knowledge base is born **with** the product: as the build-loop lands
phases, the product grows its own durable, **committed** memory, written **for agents to read** (not as
end-user docs). It is the flywheel — once seeded, every later phase (and every future `/feature` run on
this codebase) reads it first and goes faster. The `wf-documenter` agent owns it; it runs on SHIP (and
may run alongside BUILD as phases complete). Shipping the product with its own knowledge base is part
of the `/new-product` design.

Place it at `<projectRoot>/docs/knowledge/` (under the **product's** root, not the `.workflow/`
workspace). Create a root `CLAUDE.md` **in the product** that points at `docs/knowledge/INDEX.md` so any
agent (and Claude Code) bootstraps from it once the product is a real repo. Seed missing files from
`${CLAUDE_PLUGIN_ROOT}/templates/knowledge/`.

## Files

- **`INDEX.md`** — the map agents read **first**. One line per doc: link + a one-line hook + the topics
  it covers (the MEMORY.md pattern). Must stay current; it's the entry point.
- **`architecture.md`** — modules and their responsibilities, boundaries, data/control flow, the main
  entry points. The 10,000-ft view someone needs before touching anything. For greenfield this grows
  from the PRD's scope and the phase plan's vertical slices.
- **`areas/<area>.md`** — one per subsystem (from `templates/knowledge/areas/area-template.md`):
  purpose, key files (clickable paths), the public interface, invariants, gotchas, how to extend.
- **`conventions.md`** — coding patterns, naming, error handling, logging, testing patterns. So later
  coders match the house style instead of inventing their own. For greenfield, **record the stack and
  conventions the product actually adopted** (the ones decided in PRD Assumptions / phase 0).
- **`decisions/ADR-XXXX-title.md`** — lightweight ADRs (from `adr-template.md`): context, the decision,
  **why**, consequences. Add one whenever the product makes a non-obvious choice — including the big
  greenfield calls (stack, storage, the architecture the walking skeleton locked in).
- **`glossary.md`** — domain terms and entities (the domain model in words) — straight from the PRD.
- **`integrations.md`** — external services/APIs, env/config keys (names, **never secret values**).
- **`task-log.md`** — append-only ledger: per task → slug, date, what changed and **why**, link to its
  `plan.md` / `prd.md`. Gives future agents the history behind the current shape of the code.

## Principles (why this works for agents)

- **Why over what.** Code and git already show *what*. Capture the non-obvious: rationale, invariants,
  cross-cutting patterns, traps. Don't restate what a reader could trivially get from the source.
- **Link, don't copy.** Use clickable `path/to/file.py:42` references; point at the code, summarize the
  intent. Keep docs short enough to stay true.
- **`INDEX.md` is sacred.** If a doc is added/renamed/meaningfully changed, update the index in the same
  pass, or agents won't find it.
- **Freshness over completeness.** Each file carries an `updated:` line. When the documenter touches an
  area whose doc has drifted from the code, fix it or flag it `> ⚠ возможно устарело` rather than
  leaving silent rot. A small, accurate base beats a large, stale one.
- **Incremental.** The documenter seeds the base from the PRD/phase plan and then updates only the areas
  a phase touched, plus the index/task-log/ADRs. It does not try to document the whole product at once.

## Greenfield specifics

- The PRD and the phase plan are the **seed material**: the documenter mines them for `glossary.md`
  (domain terms), `architecture.md` (scope + slices), and the first ADRs (the decisions baked into the
  walking skeleton). The `prd.md` and `phase-plan.md` artifacts live in `.workflow/tasks/<slug>/`; the
  knowledge base lives in the **product** repo and links out to them where useful.
- On SHIP, after the holistic `np-judge` pass and the product README, `wf-documenter` finalizes: append
  the `task-log.md` entry, add ADRs for the notable greenfield decisions, refresh `INDEX.md` and the
  product's root `CLAUDE.md` pointer — so the very next agent inherits a warm base instead of cold code.
