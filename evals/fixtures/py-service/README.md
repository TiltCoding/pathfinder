# Fixture: py-service

A tiny dependency-free Python "reports" service used to evaluate the ai-pathfinder plugin.

- `src/reports/service.py` — `ReportService.summary()` aggregates records (has a **latent div-by-zero**
  bug on empty record lists — used by eval #2).
- `src/api/routes.py` — a minimal router; **CSV export is missing** (added by eval #1).
- `tests/` — pytest suite (passes as-is).

Run tests: `python -m pytest evals/fixtures/py-service/tests -q`

`.aipf-seed/submissions/` holds pre-seeded human feedback batches the headless eval run consumes.
Evals copy this fixture to a fresh dir before running so the original stays clean.
