# ADR-0020: Image attachments via base64-in-JSON + standalone `/attach`/`/image` endpoints

- **Date:** 2026-06-24
- **Status:** accepted
- **Task:** task-image-attachments

## Context

The human needed to attach **images** to a task from the dashboard — in **both** the chat panel and
every comment channel (block / demo-variant / thread-reply / select-to-comment) — and persist them
under the task workspace so the **orchestrator can `Read` the saved file** (visual input, e.g. fix a
UI bug from a screenshot). Inline dashboard thumbnails are secondary but in scope.

Two facts about the existing backend shaped the design:

- **`cgi`/`cgi.FieldStorage` is removed in Python 3.13** (PEP 594), and 3.13 is in the CI matrix
  (`.github/workflows/ci.yml`: 3.11/3.12/3.13 × ubuntu/macos/windows, stdlib-only). Multipart upload
  would require a new hand-rolled body reader on every supported version.
- `_read_body` (`scripts/server.py:237`) is **JSON-only** (reads exact Content-Length, parses UTF-8
  JSON → dict, no raw/binary path) and **has no size limit**. base64 text rides this path unchanged;
  `import base64` already exists in `scripts/_aipf.py:8`.

This feature is also the first deliberate departure from the ADR-0008 / ADR-0013 stance that the
companion server is **content-agnostic with 0 server changes** — see decision 4 below.

## Decisions

### 1. Transport = base64-in-JSON, NOT multipart
The client base64-encodes the image bytes into the JSON body of `POST /attach`. This keeps the upload
on the existing JSON `_read_body` path (works identically on 3.11–3.13, no `cgi`), avoids a new binary
body reader, and stays stdlib-only / CDN-free in line with `conventions.md`.

### 2. Standalone `POST /attach` (decode → validate → write → return ref)
`_attach(slug, body)` accepts `{slug, name, mime, dataB64}`, validates `mime`/extension against the
allow-list (png/jpeg/gif/webp), guards size **before and after decode**, `base64.b64decode(...,
validate=True)` in a `try`, generates a **safe server-side filename** (`att-<hex>.<ext>`, no
client-controlled separators), writes bytes via temp-then-`os.replace` under `ws.lock(slug)` to
`<root>/.workflow/tasks/<slug>/attachments/`, and returns
`{ok, file:<servername>, name:<original>, mime, bytes}`.

**Why standalone (vs inlining base64 directly in the `/chat` body):** it keeps base64 **out of
`chat.jsonl`** (the file stays lean and `Read`-able), and it shares **one** write path across every
channel — all of chat + comments already POST `/chat` via `postAnchored()`, so the upload happens once
before the message is posted regardless of channel.

### 3. `GET /image?slug=&file=` serve route
`_serve_image(slug, name)` is modeled directly on the one audited file-serving guard `_serve_mockup`
(`scripts/server.py:404`): filename regex `ATTACH_RE` (`^[A-Za-z0-9._-]{1,80}\.(png|jpe?g|gif|webp)$`),
`<task>/attachments/` dir, `realpath`+`commonpath` traversal confinement, `isfile` check, `image/*`
content-type from the extension map, `X-Content-Type-Options: nosniff`. **No CSP** (images aren't
active content) and **SVG is excluded** by the regex — the dashboard thumbnail loads bytes through this
confined route rather than a repo path (`.workflow/` is gitignored).

### 4. Message reference: `images:[{file,name,mime}]` on the `/chat` line
The client puts the `/attach` refs on the existing `/chat` message under an `images` key. `_chat_post`
(`scripts/server.py:1230`) extends its allow-list copy loop (`:1245`) from `("anchor","quote")` to also
carry `images`, optionally re-validating each item's `file` against `ATTACH_RE` (defence in depth so a
forged `images` can't smuggle a bad filename onto the line the thumbnail route then trusts). One
allow-list line lights up **all** channels (chat + variant + thread-reply + select-to-comment) because
they all funnel through `postAnchored()` → `/chat`. `_draft_add` is **not** touched.

### 5. Size cap (none existed before)
5 MB/image, 6 images/message, enforced on both client (clean reject + toast) and server (reject on
encoded length first, re-check decoded length, never `500`). `_read_body` was previously **uncapped** —
this is the first byte limit on any request body and was the top risk flagged in exploration.

## Why this is an explicit, deliberate exception to ADR-0008's "0 server changes"

ADR-0008 (and ADR-0013) established that feedback features ride the **existing** contract with the
backend **agnostic to item content** — new feedback either fits the fixed item fields or it doesn't
ship. This feature **cannot** fit that mold: persisting raw image bytes for the orchestrator to `Read`
requires a real on-disk file, which requires a write+serve path the server does not have. We therefore
**knowingly** add:

- two new endpoints (`POST /attach`, `GET /image`), and
- one new key (`images`) to the `_chat_post` allow-list.

This is recorded so it is **not re-litigated** as a contract violation: it is a scoped, intentional
extension justified by the "agent reads a real file" requirement, kept minimal (two endpoints + one
allow-list key, no change to `_draft_add`/`_submit`, message refs not bytes on the `chat.jsonl` line).

## Consequences

- **For the orchestrator/agent:** a `chat.jsonl` line may now carry `images:[{file,name,mime}]`; the
  saved bytes live at the **absolute** path `<root>/.workflow/tasks/<slug>/attachments/<file>` and are
  read with `Read`. `name` is the original client filename (metadata only); `file` is the safe
  server-generated on-disk name.
- **First request-body size cap** in the server — future uploads should reuse the same guard pattern.
- **Pending-attachment state lives in a module-level var** keyed by anchor/`"chat"` on the front-end
  (mirrors how `draftItems` survives), so the 3s `#content` re-render does not wipe an unsent image.
- The content-agnostic invariant now has a **documented exception**; future binary-persistence features
  should follow this shape (standalone endpoint + confined serve route mirroring `_serve_mockup`)
  rather than widening item fields.

## Links

- **ADR-0008** (feedback over existing contract / 0 server changes / content-agnostic backend) — the
  stance this ADR makes a **deliberate, scoped exception** to.
- **ADR-0010** (shared store via worktree symlink) — attachments live under `workspace.task_dir(slug)`
  (the shared store), NOT the git worktree; `.workflow/` is gitignored.
- **ADR-0016** (`/ask` read-only over existing contract) — same lineage of "ride the contract, minimal
  server change"; this feature is the case where that wasn't sufficient.
- **ADR-0018** (i18n) — the attach UI strings follow the `STR{en,ru}` + `HUB_PAGE` duplication +
  completeness smoke-test convention.
