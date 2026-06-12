# Rubric: plan quality (`plan.md`)

Judge the produced `plan.md` for an eval run. Each criterion is pass/fail with evidence.

- **Blocks with stable ids** — the plan is decomposed into blocks each with an id (b1, b2, …) and a clear title.
- **Coverage** — every part of the task prompt and acceptance criterion maps to at least one block.
- **Concrete** — blocks name real files/functions (clickable paths), not vague intentions.
- **Reuse** — the plan builds on existing patterns/utilities found in exploration rather than inventing parallel structures.
- **Right altitude** — enough detail for a coder to execute without re-planning, without dictating every line.
- **Work-streams** — independent, parallelizable units are identified.
- **Verification** — the plan states how the change will be verified (tests/commands).

A good plan is minimal-but-complete: the smallest change that fully satisfies the task.
