# ADR-0025: Artifacts panel — browsable agent deliverables via a confined-serve route

- **Date:** 2026-06-28
- **Status:** accepted
- **Task:** artifacts-panel-tab (improve-platform-vision, feat-17 / cand-4; folds in cand-6 security armor)

## Context

The agent already materializes tangible outputs — demo/redesign mockups, infographics, process
diagrams (`mockups/*.html|svg`), and plans/docs — but they were scattered across the Workflow demo card,
the Changes diff, and the Documentation tab, with no single place to browse, preview, version, and
download them. The request asked for an **Artifacts-from-Claude analog**: a first-class panel of the
agent's deliverables.

The hard constraint is **security**. An artifacts panel renders **agent-generated HTML/SVG** — a fresh
XSS/injection surface. The only safe precedent in the codebase is the demo iframe: served from `/mockup`
with `MOCKUP_CSP` + `X-Content-Type-Options: nosniff`, rendered in a `sandbox="allow-scripts"` iframe
**without** `allow-same-origin`. If the panel were built naively (e.g. fetch the file and `innerHTML` it,
or serve it as same-origin HTML), agent HTML would execute in the dashboard's origin with access to
`/submit`, `/chat`, `/attach` — a real account-takeover-class hole on the local server.

## Decision

Add an **Artifacts tab** (the 5th dashboard tab) backed by **two read-only endpoints**, with the
security invariant baked in from the start (cand-6):

1. **`GET /artifacts?slug=`** — a JSON *listing* (metadata only) of files under `<task>/mockups/` **and**
   `<task>/artifacts/` that match a strict name allowlist (`ARTIFACT_RE` —
   `html|svg|md|json|txt|csv|patch|diff|png|jpe?g|gif|webp`). Each entry carries `{name, dir, kind,
   active, size, mtime, base, version}`; `base`/`version` split a `<name>.v<N>.<ext>` convention for
   client-side version grouping (pairs with feat-20).

2. **`GET /artifact?slug=&file=[&download=1]`** — serve **one** file, **confined** exactly like
   `_serve_mockup`: `realpath` + `commonpath` must stay inside the task's `mockups/`/`artifacts/` dir, the
   name must match `ARTIFACT_RE` (no path separators). Serving modes:
   - **Active content (`html`/`svg`)** → served with **`MOCKUP_SEC_HEADERS`** (the sandbox CSP +
     `nosniff`). The dashboard renders it **only inside a `sandbox="allow-scripts"` iframe** (no
     `allow-same-origin`) + `referrerpolicy="no-referrer"` — **never `innerHTML`**. This is the invariant.
   - **Inert content** (md/json/txt/diff/images) → `nosniff` only; images previewed via `<img>`.
   - **`download=1`** → `Content-Disposition: attachment` + `nosniff` (the browser saves rather than
     renders), so any allowed type is downloadable without executing.

The frontend adds the tab, a signature-guarded gallery grouped by `base` (newest version first), a
per-card Preview (iframe/`<img>`) + Download, polled on the active tab and gated on `document.hidden`
(reusing the feat-10 pattern). **0 changes to the existing demo/mockup machinery** — the new route
reuses the same confinement + CSP, so there is exactly one security model for agent content.

## Consequences

- **The XSS surface is closed by construction**: agent HTML can only run inside a sandbox iframe served
  from a confined CSP route; it can never reach the dashboard's origin or its POST endpoints. The
  invariant is pinned by `tests/test_artifacts.py` (active → CSP+nosniff; download → attachment;
  traversal/bad-name → 404) — a regression fails CI.
- **`<task>/artifacts/` is the new contract** for non-mockup deliverables: an orchestrator that wants a
  file to show up in the panel writes it there (or to `mockups/` for demos). Versioning is the
  `<name>.v<N>.<ext>` convention (feat-20 deepens diff-across-versions).
- Reuses `MOCKUP_CSP`/`MOCKUP_SEC_HEADERS` and the `_serve_mockup` traversal pattern, so the security
  model doesn't fork (lineage ADR-0020 attachments, the markdown-url-sanitize and security-headers work).
- The listing rides the existing `/hub.json`-style polling; no new persistent state, no token/cost
  surface. Lineage of "ride the contract, 0 server bloat where possible" (ADR-0008/0013/0016) — here the
  two new GET routes are a deliberate, security-motivated addition (like ADR-0020's `/attach`/`/image`).
