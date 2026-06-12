# Rubric: PRD quality (`prd.md`)

Judge the `prd.md` produced by an eval run of `/new-product`. Each criterion is pass/fail with evidence.

- **FR-IDs present** — functional requirements live in a table where each row has a stable id (FR-1, FR-2, …), not a loose prose list.
- **Given-When-Then per FR** — every FR carries an acceptance scenario in Given-When-Then form, concrete enough to turn into a test.
- **Assumptions captured** — an explicit Assumptions section records what was assumed (unanswered elicitation → assumption), so nothing silently floats.
- **Non-goals explicit** — out-of-scope items are stated as Non-goals, so scope can't creep into BUILD.
- **Measurable goals** — product goals/success metrics are stated in checkable terms, not vague aspirations.
- **Traceability FR→test→rubric** — each FR points forward to the test(s) and the judge-rubric line that will verify it, so coverage is auditable end to end.
- **Lean-adaptive** — the FR table and Assumptions are always present; other sections are sized to the product's complexity rather than padded.

A good PRD is machine-readable: every FR id can be followed to a Given-When-Then, a test, and a rubric row.
