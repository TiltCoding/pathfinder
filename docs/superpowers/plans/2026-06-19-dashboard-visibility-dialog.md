# Dashboard visibility + in-context dialog — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the per-task dashboard show what the agent is doing now + work-stream progress, and turn one-shot comments into in-context threaded discussions where the agent can ask back.

**Architecture:** Everything rides the existing file contract — the agent fills fields in `dashboard.json` / `chat.jsonl`; the dashboard renders and re-renders on a changed stamp. The only server change is passing two extra fields (`anchor`, `quote`) through `POST /chat`. Threaded discussion reuses the proven `chat.jsonl` + `/wait` channel, now optionally anchored to a block/region/variant.

**Tech Stack:** Python 3 stdlib `http.server` (`scripts/server.py`), a single static `templates/dashboard.html` (vanilla JS, no build), stdlib `unittest` tests (offline, tempfile-based, cross-platform).

**Spec:** `docs/superpowers/specs/2026-06-19-dashboard-visibility-dialog-design.md`

---

## File structure

| File | Responsibility | Change |
|------|----------------|--------|
| `templates/dashboard.html` | All client rendering & interaction | A1 line, A2 chip, anchored threads, reroute comments, markers/counter |
| `scripts/server.py` | File-backed HTTP contract | `_chat_post`: pass `anchor`/`quote` through |
| `tests/test_chat_anchor.py` | Pin the server change | new test file |
| `skills/feature/dashboard-guide.md`, `skills/feature/feedback-loop.md`, `skills/feature/state-schema.md` | Orchestrator rules | document `now`/`nowAt` + anchored chat / `needsAnswer` |
| `skills/{ask,improve,new-product}/*` | Mirror the rule | analogous note where the file exists |
| `docs/knowledge/areas/dashboard-feedback-ui.md`, `docs/knowledge/INDEX.md` | Durable KB | record the new model (VERIFY) |

**Stamp evolution.** `tick()` re-renders only when a JSON "stamp" changes (`templates/dashboard.html:2356`). Tasks below grow it incrementally; the final form is:

```js
const stamp = JSON.stringify([data.updatedAt, data.phase, data.status, data.now,
  (data.workstreams||[]).map(w=>w.status).join(","),
  (replies.replies||[]).length, chatMsgs.length]);
```

---

## Task 1: A1 — «Сейчас: …» line (process visibility)

**Files:**
- Modify: `templates/dashboard.html` (CSS ~line 74; header markup ~line 471; `render()` ~line 813; helpers near `function card` ~line 884; `tick()` ~line 2356)

This is client-only; the agent already writes `dashboard.json` and `/data` serves it verbatim (`scripts/server.py:200`), so `now`/`nowAt` pass through with no server change. Verification is manual via `/run` with a crafted fixture (the project does not unit-test client rendering — see `docs/knowledge/conventions.md`).

- [ ] **Step 1: Add CSS for the line**

After the `.progress > div { … }` rule (`templates/dashboard.html:76`), add:

```css
  .now-line { font-size: 12px; color: var(--muted); margin-top: 6px; padding: 0 2px; }
  .now-line.stale { opacity: .5; }
  .now-line b { color: var(--ink); font-weight: 600; }
```

- [ ] **Step 2: Add the header element**

Immediately after the progress row (`templates/dashboard.html:471`, the `<div class="top-inner"><div class="progress"…></div>` line), add a new row:

```html
  <div class="top-inner"><div class="now-line" id="now-line" hidden></div></div>
```

- [ ] **Step 3: Add render helpers**

Just above `function card(t, body){…}` (`templates/dashboard.html:884`), add:

```js
function nowAge(ts){
  if(!ts) return null;
  const t = Date.parse(ts); if(isNaN(t)) return null;
  return Math.max(0, (Date.now() - t) / 1000);
}
function fmtAge(sec){
  if(sec < 60) return Math.round(sec) + "с назад";
  if(sec < 3600) return Math.round(sec/60) + "м назад";
  return Math.round(sec/3600) + "ч назад";
}
function renderNow(data){
  const el = $("#now-line"); if(!el) return;
  const now = (data.now || "").trim();
  if((data.status||"working") === "awaiting-batch" || !now){ el.hidden = true; return; }
  el.hidden = false;
  const age = nowAge(data.nowAt);
  const stale = age != null && age > 90;
  el.classList.toggle("stale", stale);
  el.innerHTML = "Сейчас: <b>" + esc(now) + "</b>" + (age != null && !stale ? " · " + esc(fmtAge(age)) : "");
}
```

- [ ] **Step 4: Call it from `render()`**

In `render()`, right after the actionbar `awaiting` toggle (`templates/dashboard.html:813`, the `$("#actionbar").classList.toggle("awaiting", …)` line), add:

```js
  renderNow(data);
```

- [ ] **Step 5: Keep the relative time fresh + extend the stamp**

In `tick()` (`templates/dashboard.html:2356-2357`), replace:

```js
    const stamp = JSON.stringify([data.updatedAt, data.phase, data.status, (replies.replies||[]).length]);
    if(stamp !== lastDataStamp){ lastDataStamp = stamp; render(data, replies); }
```

with:

```js
    const stamp = JSON.stringify([data.updatedAt, data.phase, data.status, data.now, (replies.replies||[]).length]);
    if(stamp !== lastDataStamp){ lastDataStamp = stamp; render(data, replies); }
    else if(lastData){ renderNow(lastData); }   // tick the "N с назад" / staleness without a full re-render
```

- [ ] **Step 6: Manual verification**

Create a scratch fixture and open the page:

```bash
mkdir -p .workflow/tasks/_scratch_a1
cat > .workflow/tasks/_scratch_a1/dashboard.json <<'JSON'
{"title":"Scratch A1","phase":"IMPLEMENT","status":"working","now":"исследую server.py","nowAt":"2026-06-19T12:00:00","progress":{"done":1,"total":3},"summary":"тест"}
JSON
python dev.py   # or scripts/server.py per docs/knowledge/architecture.md
```

Open `/?slug=_scratch_a1`. Expected: header shows `Сейчас: исследую server.py` (greyed/stale because `nowAt` is old). Edit `now`/`nowAt` to a fresh time → it shows `· Nс назад`; set `status:"awaiting-batch"` → the line hides. Then `rm -rf .workflow/tasks/_scratch_a1`.

- [ ] **Step 7: Commit**

```bash
git add templates/dashboard.html
git commit -m "feat(dashboard): «Сейчас: …» строка текущей активности агента (A1)"
```

---

## Task 2: A2 — work-stream summary chip (progress visibility)

**Files:**
- Modify: `templates/dashboard.html` (CSS ~line 76; progress row ~line 471; `render()` progress block ~line 814-815; helpers ~line 884; stamp ~line 2356)

`workstreams` already exist in `dashboard.json` and render as the "Work-streams" card (`templates/dashboard.html:869-875`). This adds a header summary and makes the progress bar derive from work-streams when present (one source of truth).

- [ ] **Step 1: Add CSS for the chip**

After the `.now-line b { … }` rule added in Task 1, add:

```css
  .ws-summary { display: inline-flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); white-space: nowrap; }
  .ws-summary .bar { display: inline-flex; height: 8px; width: 84px; border-radius: 999px; overflow: hidden; background: var(--line); flex: none; }
  .ws-summary .bar > i { height: 100%; display: block; }
  .ws-summary .bar > i.done { background: var(--ok); }
  .ws-summary .bar > i.in_progress { background: var(--accent); }
  .ws-summary b { color: var(--ink); font-weight: 600; }
```

- [ ] **Step 2: Put the chip in the progress row**

Replace the progress row (`templates/dashboard.html:471`) — currently:

```html
  <div class="top-inner"><div class="progress" style="flex:1"><div id="progress-bar"></div></div></div>
```

with (note: the `now-line` row from Task 1 stays directly below this):

```html
  <div class="top-inner">
    <div class="progress" style="flex:1"><div id="progress-bar"></div></div>
    <span class="ws-summary" id="ws-summary" hidden></span>
  </div>
```

- [ ] **Step 3: Add helpers**

Just above `function card(t, body){…}` (`templates/dashboard.html:884`), add:

```js
function wsCounts(list){
  const c = { done:0, in_progress:0, todo:0, total:(list||[]).length };
  for(const ws of (list||[])){ const s = ws.status||"todo"; if(c[s]!=null) c[s]++; else c.todo++; }
  return c;
}
function renderWsSummary(data){
  const el = $("#ws-summary"); if(!el) return;
  const list = data.workstreams || [];
  if(!list.length){ el.hidden = true; return; }
  const c = wsCounts(list);
  const pct = n => c.total ? Math.round(100 * n / c.total) : 0;
  el.hidden = false;
  el.innerHTML =
    '<span class="bar"><i class="done" style="width:' + pct(c.done) + '%"></i>' +
    '<i class="in_progress" style="width:' + pct(c.in_progress) + '%"></i></span>' +
    '<span><b>' + c.done + '/' + c.total + '</b> готово · ' + c.in_progress + ' в работе · ' + c.todo + ' в очереди</span>';
}
```

- [ ] **Step 4: Make the progress bar derive from work-streams; call the chip**

In `render()`, replace the progress block (`templates/dashboard.html:814-815`):

```js
  const pr = data.progress || {done:0,total:0};
  $("#progress-bar").style.width = (pr.total ? Math.round(100*pr.done/pr.total) : 0) + "%";
```

with:

```js
  const wl = data.workstreams || [];
  const pr = wl.length ? (() => { const c = wsCounts(wl); return {done:c.done, total:c.total}; })()
                       : (data.progress || {done:0, total:0});
  $("#progress-bar").style.width = (pr.total ? Math.round(100*pr.done/pr.total) : 0) + "%";
  renderWsSummary(data);
```

- [ ] **Step 5: Extend the stamp so status changes in work-streams re-render**

In `tick()` update the stamp added in Task 1 to include a work-stream digest:

```js
    const stamp = JSON.stringify([data.updatedAt, data.phase, data.status, data.now,
      (data.workstreams||[]).map(w=>w.status).join(","), (replies.replies||[]).length]);
```

- [ ] **Step 6: Manual verification**

```bash
mkdir -p .workflow/tasks/_scratch_a2
cat > .workflow/tasks/_scratch_a2/dashboard.json <<'JSON'
{"title":"Scratch A2","phase":"IMPLEMENT","status":"working","workstreams":[{"id":"ws1","title":"A","status":"done"},{"id":"ws2","title":"B","status":"done"},{"id":"ws3","title":"C","status":"in_progress"},{"id":"ws4","title":"D","status":"todo"}],"summary":"тест"}
JSON
python dev.py
```

Open `/?slug=_scratch_a2`. Expected: chip reads `2/4 готово · 1 в работе · 1 в очереди`, the mini-bar is ~50% green + ~25% accent, and the top progress bar is 50%. Remove `workstreams`, add `"progress":{"done":1,"total":2}` → chip hides, bar is 50% from `progress`. Then `rm -rf .workflow/tasks/_scratch_a2`.

- [ ] **Step 7: Commit**

```bash
git add templates/dashboard.html
git commit -m "feat(dashboard): сводка work-streams в шапке + единый источник прогресс-бара (A2)"
```

---

## Task 3: B1 server — pass `anchor`/`quote` through `POST /chat`

**Files:**
- Modify: `scripts/server.py` (`_chat_post`, lines 1148-1163)
- Test: `tests/test_chat_anchor.py` (create)

`needsAnswer` is **not** accepted from the human POST — only the agent sets it by appending its own line directly to `chat.jsonl`. The human POST may carry `anchor` (which block/region/variant) and `quote` (the selected fragment, for select-to-comment).

- [ ] **Step 1: Write the failing test**

Create `tests/test_chat_anchor.py`:

```python
#!/usr/bin/env python3
"""Offline tests for anchored chat: `POST /chat` carries optional `anchor`/`quote`
through into chat.jsonl, stays backward-compatible without them, and still rejects
empty text. stdlib unittest, no network, tempfile only.

Run:
    python -m unittest tests.test_chat_anchor
    python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import server  # noqa: E402


class _ChatHandler:
    """Drives the real `_chat_post` offline: real workspace + signal/json/send,
    socket and waker stubbed (we assert on the file, not the wake plumbing)."""

    def __init__(self, workspace):
        self.workspace = workspace
        self.status = None
        self.headers = {}
        self._chunks = []

    # BaseHTTPRequestHandler surface used by _send
    def send_response(self, code): self.status = code
    def send_header(self, k, v): self.headers[k] = v
    def end_headers(self): pass
    @property
    def wfile(self): return self
    def write(self, data): self._chunks.append(data)
    @property
    def body(self): return b"".join(self._chunks)

    # stub the wake plumbing (no /wait long-poll in a unit test)
    def _wake(self, slug): pass

    # bound real handler methods
    _send = server.Handler._send
    _json = server.Handler._json
    _append_signal = server.Handler._append_signal
    _chat_post = server.Handler._chat_post

    def post(self, slug, body):
        self._chat_post(slug, body)
        return json.loads(self.body.decode("utf-8"))


class ChatAnchorTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)
        self.ws = server.Workspace(self.root)
        os.makedirs(self.ws.tasks, exist_ok=True)
        self.h = _ChatHandler(self.ws)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _last_msg(self, slug):
        path = self.ws.task_file(slug, "chat.jsonl")
        with open(path, "r", encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        return json.loads(lines[-1])

    def test_anchor_and_quote_persist(self):
        r = self.h.post("t1", {"text": "поправь тут", "anchor": "b2", "quote": "эту фразу"})
        self.assertTrue(r.get("ok"))
        m = self._last_msg("t1")
        self.assertEqual(m["anchor"], "b2")
        self.assertEqual(m["quote"], "эту фразу")
        self.assertEqual(m["role"], "human")

    def test_absent_fields_not_written(self):
        self.h.post("t2", {"text": "просто сообщение"})
        m = self._last_msg("t2")
        self.assertNotIn("anchor", m)
        self.assertNotIn("quote", m)

    def test_needsanswer_from_human_is_ignored(self):
        # Only the agent may mark a turn as needing an answer.
        self.h.post("t3", {"text": "вопрос?", "needsAnswer": True})
        m = self._last_msg("t3")
        self.assertNotIn("needsAnswer", m)

    def test_empty_text_rejected(self):
        r = self.h.post("t4", {"text": "   ", "anchor": "b1"})
        self.assertEqual(self.h.status, 400)
        self.assertIn("error", r)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m unittest tests.test_chat_anchor -v`
Expected: `test_anchor_and_quote_persist` FAILS with `KeyError: 'anchor'` (the field is dropped today); the other three pass already.

- [ ] **Step 3: Implement the passthrough**

In `scripts/server.py`, in `_chat_post` (lines 1157-1158), replace:

```python
        msg = {"role": "human", "text": text, "ts": now_iso(),
               "phase": body.get("phase")}
```

with:

```python
        msg = {"role": "human", "text": text, "ts": now_iso(),
               "phase": body.get("phase")}
        # Optional anchoring: which block/region/variant this turn discusses, and
        # the quoted fragment for select-to-comment. Copied verbatim (the backend
        # stays agnostic to content). `needsAnswer` is intentionally NOT accepted
        # from the human — only the agent sets it on its own appended turns.
        for _k in ("anchor", "quote"):
            _v = body.get(_k)
            if _v:
                msg[_k] = _v
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest tests.test_chat_anchor -v`
Expected: all four tests PASS.

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `python -m unittest discover -s tests`
Expected: OK (no failures).

- [ ] **Step 6: Commit**

```bash
git add scripts/server.py tests/test_chat_anchor.py
git commit -m "feat(server): прокидывание anchor/quote через POST /chat (B1)"
```

---

## Task 4: B1 client — render anchored threads (read-only display)

**Files:**
- Modify: `templates/dashboard.html` (thread CSS ~line 419; `regionFooter`/`region`/`renderDemo` ~line 886-932; `render()` ~line 817-883; `chatTick`/`renderChat` ~line 2307-2335; stamp ~line 2356)

After this task, anchored chat messages render as threads under their block/region/variant; the global chat panel shows only un-anchored messages. Inputs are added in Task 5.

- [ ] **Step 1: Add thread CSS**

After the `.msg .ts { … }` rule (`templates/dashboard.html:419`), add:

```css
  .thread { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; }
  .th-turn { max-width: 92%; padding: 7px 10px; border-radius: 10px; font-size: 13px; line-height: 1.5; }
  .th-turn.human { align-self: flex-end; background: var(--accent-soft); }
  .th-turn.agent { align-self: flex-start; background: var(--chip); }
  .th-turn .th-who { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; opacity: .7; margin-bottom: 2px; }
  .th-turn .th-quote { color: var(--muted); border-left: 3px solid var(--accent); padding: 1px 8px; margin-bottom: 4px; white-space: pre-wrap; }
  .th-needs { color: var(--warn); font-weight: 700; }
  .th-status { font-size: 11px; color: var(--ok); margin-top: 2px; }
  .th-reply textarea { min-height: 38px; margin-top: 6px; }
  .block.thread-open, .region.thread-open, .variant.thread-open { box-shadow: inset 3px 0 0 var(--warn); }
```

- [ ] **Step 2: Add the thread-building helper**

Just above `function card(t, body){…}` (`templates/dashboard.html:884`), add:

```js
// Bucket chat messages that carry an `anchor` into per-anchor threads (ordered as stored).
function anchoredThreads(){
  const by = {};
  for(const m of chatMsgs){ if(m.anchor){ (by[m.anchor] = by[m.anchor] || []).push(m); } }
  return by;
}
function threadTurns(msgs){
  let html = "";
  for(const m of msgs){
    const quote = m.quote ? '<div class="th-quote">' + esc(m.quote) + '</div>' : "";
    const needs = (m.role === "agent" && m.needsAnswer) ? ' <span class="th-needs">нужен ваш ответ</span>' : "";
    const who = (m.role === "agent" ? "агент" : "вы") + needs;
    const text = m.role === "agent" ? md(m.text) : esc(m.text);
    html += '<div class="th-turn ' + (m.role === "agent" ? "agent" : "human") + '">' +
            quote + '<div class="th-who">' + who + '</div><div class="th-text">' + text + '</div></div>';
  }
  // "учтено агентом": last turn is an agent reply (not itself a question) following a human turn
  const last = msgs[msgs.length - 1];
  const hadHuman = msgs.some(x => x.role === "human");
  if(last && last.role === "agent" && !last.needsAnswer && hadHuman){
    html += '<div class="th-status">✓ учтено агентом</div>';
  }
  return html;
}
```

- [ ] **Step 3: Render threads + the (still-unwired) reply input in `regionFooter`**

Replace `regionFooter` (`templates/dashboard.html:894-899`):

```js
function regionFooter(anchor, repliesByBlock){
  const reps = (repliesByBlock||{})[anchor] || [];
  const drafts = draftItems.filter(i => i.blockId === anchor);
  return reps.map(r=>`<div class="reply"><div class="who">агент</div>${md(r.text)}</div>`).join("")
       + drafts.map(d=>cmtCard(d)).join("");
}
```

with:

```js
function regionFooter(anchor, repliesByBlock, threadsByAnchor){
  const reps = (repliesByBlock||{})[anchor] || [];        // legacy replies.json (kept for back-compat)
  const drafts = draftItems.filter(i => i.blockId === anchor);  // removed in Task 5
  const msgs = (threadsByAnchor||{})[anchor] || [];
  const thread = msgs.length ? `<div class="thread">${threadTurns(msgs)}</div>` : "";
  const input = `<div class="th-reply"><textarea data-thread="${esc(anchor)}" placeholder="Ответить в этой ветке… (Enter — отправить)"></textarea></div>`;
  return reps.map(r=>`<div class="reply"><div class="who">агент</div>${md(r.text)}</div>`).join("")
       + drafts.map(d=>cmtCard(d)).join("")
       + thread + input;
}
```

- [ ] **Step 4: Thread `threadsByAnchor` through `region` and `renderDemo`**

Replace `region` (`templates/dashboard.html:886-892`) signature + footer call:

```js
function region(anchor, innerHtml, repliesByBlock, threadsByAnchor){
  const has = draftItems.some(i => i.blockId === anchor);
  return `<div class="region ${has?'has-draft':''}" data-anchor="${esc(anchor)}">
    <div class="md">${innerHtml}</div>
    ${regionFooter(anchor, repliesByBlock, threadsByAnchor)}
  </div>`;
}
```

In `renderDemo` change the signature (`templates/dashboard.html:905`) to accept the threads and pass them to the footer it builds (`templates/dashboard.html:927`):

```js
function renderDemo(demo, repliesByBlock, threadsByAnchor){
```
```js
      <div class="v-body">${cap}${comment}${regionFooter(vr.id, repliesByBlock, threadsByAnchor)}</div>
```

- [ ] **Step 5: Build `threadsByAnchor` in `render()` and pass it to every caller**

In `render()`, just after the `repliesByBlock`/`repliesByQ` bucketing block (`templates/dashboard.html:817-821`), add:

```js
  const threadsByAnchor = anchoredThreads();
```

Then update the four call sites to pass it:

- `templates/dashboard.html:824` → `region("summary", md(data.summary), repliesByBlock, threadsByAnchor)`
- `templates/dashboard.html:825` → `region("codebaseMap", md(data.codebaseMap), repliesByBlock, threadsByAnchor)`
- `templates/dashboard.html:827` → `renderDemo(data.demo, repliesByBlock, threadsByAnchor)`
- `templates/dashboard.html:837` (inside the plan-block loop) → `${regionFooter(blk.id, repliesByBlock, threadsByAnchor)}`

- [ ] **Step 6: Show only un-anchored messages in the global chat panel**

In `renderChat` (`templates/dashboard.html:2325-2335`), make it operate on a filtered list. Replace the first two lines of the body:

```js
function renderChat(){
  const log = $("#chat-log");
  if(!chatMsgs.length){ log.innerHTML = `<p class="empty">Сообщений пока нет. Напишите агенту — он прочитает и ответит на ближайшем чекпоинте.</p>`; return; }
```

with:

```js
function renderChat(){
  const log = $("#chat-log");
  const global = chatMsgs.filter(m => !m.anchor);   // anchored messages live in their thread, not here
  if(!global.length){ log.innerHTML = `<p class="empty">Сообщений пока нет. Напишите агенту — он прочитает и ответит на ближайшем чекпоинте.</p>`; return; }
```

and change the `.map` source on the next statement from `chatMsgs.map(...)` to `global.map(...)` (`templates/dashboard.html:2329`).

- [ ] **Step 7: Re-render threads when chat changes; base unread on global only**

In `chatTick` (`templates/dashboard.html:2307-2317`), after `if(changed) renderChat();` add a `rerender()` so anchored threads refresh:

```js
    if(changed){ renderChat(); rerender(); }
```
(remove the old standalone `if(changed) renderChat();` line).

In `updateChatUnread` (`templates/dashboard.html:2318-2324`), base counts on the un-anchored messages so threads don't inflate the badge:

```js
function updateChatUnread(){
  const b = $("#chat-unread");
  const globalCount = chatMsgs.filter(m => !m.anchor).length;
  if(chatOpen){ chatSeen = globalCount; b.hidden = true; return; }
  const unread = Math.max(0, globalCount - chatSeen);
  if(unread > 0){ b.hidden = false; b.textContent = unread > 9 ? "9+" : unread; }
  else b.hidden = true;
}
```

Also update `toggleChat` (`templates/dashboard.html:2298`) `chatSeen = chatMsgs.length;` → `chatSeen = chatMsgs.filter(m => !m.anchor).length;`.

- [ ] **Step 8: Add `chatMsgs.length` to the stamp**

Final stamp form in `tick()`:

```js
    const stamp = JSON.stringify([data.updatedAt, data.phase, data.status, data.now,
      (data.workstreams||[]).map(w=>w.status).join(","), (replies.replies||[]).length, chatMsgs.length]);
```

- [ ] **Step 9: Manual verification**

```bash
mkdir -p .workflow/tasks/_scratch_b1
cat > .workflow/tasks/_scratch_b1/dashboard.json <<'JSON'
{"title":"Scratch B1","phase":"ELABORATE","status":"awaiting-batch","summary":"Сводка для обсуждения.","planBlocks":[{"id":"b1","title":"Блок 1","body":"Тело блока 1."}]}
JSON
printf '%s\n' \
 '{"role":"human","text":"А почему так?","anchor":"b1","quote":"Тело блока 1.","ts":"2026-06-19T12:00:00"}' \
 '{"role":"agent","text":"Потому что *X*.","anchor":"b1","needsAnswer":true,"ts":"2026-06-19T12:01:00"}' \
 '{"role":"human","text":"свободное сообщение в чат","ts":"2026-06-19T12:02:00"}' \
 > .workflow/tasks/_scratch_b1/chat.jsonl
python dev.py
```

Open `/?slug=_scratch_b1`. Expected: under block **b1** a thread shows the human turn (with the quote), then the agent turn marked **нужен ваш ответ**; a disabled-looking reply textarea sits below. The 💬 chat panel shows only `свободное сообщение в чат`. Then `rm -rf .workflow/tasks/_scratch_b1`.

- [ ] **Step 10: Commit**

```bash
git add templates/dashboard.html
git commit -m "feat(dashboard): рендер anchored-веток обсуждения под блоками/регионами/вариантами (B1)"
```

---

## Task 5: B1 client — route comments into anchored chat; retire draft-comments

**Files:**
- Modify: `templates/dashboard.html` (`wireBlocks` ~line 936-969; `captureActiveInput` ~line 972-979; comment senders ~line 1094-1107; `highlightComments` call ~line 881)

Select-to-comment and the per-variant comment box now post immediately as anchored chat messages. The draft `kind:"comment"` path (cards, highlight, edit-queue count) is removed; drafts keep only `answer` (questions) and demo selection.

- [ ] **Step 1: Add a shared poster + retarget the three comment entry points**

Replace `sendComment` and `saveVariantComment` (`templates/dashboard.html:1094-1107`):

```js
async function sendComment(){
  const text = popInput.value.trim();
  if(!text || !selTarget) return;
  await postAnchored(selTarget.anchor, text, selTarget.text);
  hidePopover();
}
async function saveVariantComment(variantId, value, ta){
  const text = (value || "").trim();
  if(!text || !variantId) return;
  if(ta) ta.value = "";
  await postAnchored(variantId, text, "");
}
// One path for every anchored turn (select-to-comment, variant comment, thread reply).
async function postAnchored(anchor, text, quote){
  const t = (text || "").trim();
  if(!t || !anchor) return;
  const body = { slug:SLUG, anchor, text:t, phase:(lastData&&lastData.phase)||"" };
  if(quote) body.quote = quote;
  await api("/chat", { method:"POST", body: JSON.stringify(body) });
  await chatTick();
  toast("Отправлено агенту");
}
```

- [ ] **Step 2: Wire the thread reply textareas**

In `wireBlocks` (`templates/dashboard.html:936`), after the existing `data-comment-variant` keydown wiring block (`templates/dashboard.html:961-965`), add wiring for thread inputs:

```js
  document.querySelectorAll("textarea[data-thread]").forEach(ta =>
    ta.onkeydown = (e) => {
      if(e.key === "Enter" && !e.shiftKey){ e.preventDefault();
        postAnchored(ta.getAttribute("data-thread"), ta.value, ""); }
    });
```

- [ ] **Step 3: Preserve thread-input focus/caret across re-render**

In `captureActiveInput` (`templates/dashboard.html:976-977`), extend the attribute allow-list so a thread textarea survives the innerHTML swap:

```js
  if(el.getAttribute("data-answer") !== null) attr = "data-answer";
  else if(el.getAttribute("data-comment-variant") !== null) attr = "data-comment-variant";
  else if(el.getAttribute("data-thread") !== null) attr = "data-thread";
```

- [ ] **Step 4: Stop creating/rendering draft comments**

The select-to-comment and variant paths no longer write `kind:"comment"` drafts (Step 1). Remove the now-dead draft-comment rendering so old cards don't double up with threads:

- In `regionFooter` (edited in Task 4) delete the `drafts`/`cmtCard` parts so it reads:

```js
function regionFooter(anchor, repliesByBlock, threadsByAnchor){
  const reps = (repliesByBlock||{})[anchor] || [];        // legacy replies.json (kept for back-compat)
  const msgs = (threadsByAnchor||{})[anchor] || [];
  const thread = msgs.length ? `<div class="thread">${threadTurns(msgs)}</div>` : "";
  const input = `<div class="th-reply"><textarea data-thread="${esc(anchor)}" placeholder="Ответить в этой ветке… (Enter — отправить)"></textarea></div>`;
  return reps.map(r=>`<div class="reply"><div class="who">агент</div>${md(r.text)}</div>`).join("")
       + thread + input;
}
```

- In `render()` remove the `highlightComments();` call (`templates/dashboard.html:881`) — selection highlights came from draft `selectedText`, which no longer exists (the quote now lives in the thread turn). `highlightComments`/`applyHighlights`/`wrapRange`/`cmtCard` become dead code; leave them unused (no caller) to keep this diff focused.

- [ ] **Step 5: Manual verification**

```bash
mkdir -p .workflow/tasks/_scratch_b1b
cat > .workflow/tasks/_scratch_b1b/dashboard.json <<'JSON'
{"title":"Scratch B1b","phase":"ELABORATE","status":"awaiting-batch","summary":"Выдели меня и прокомментируй.","planBlocks":[{"id":"b1","title":"Блок","body":"Текст блока."}]}
JSON
python dev.py
```

Open `/?slug=_scratch_b1b`. (1) Select text in the summary → popover → type a comment → Enter. Expected: a toast «Отправлено агенту», and within ~5s a human turn (with the quoted fragment) appears in the summary thread; the edit-queue count in the action bar stays **0** (no draft). (2) Type in a thread reply box → Enter → it appears as a new human turn. Confirm `chat.jsonl` in the task dir has lines with `"anchor":"summary"`. Then `rm -rf .workflow/tasks/_scratch_b1b`.

- [ ] **Step 6: Commit**

```bash
git add templates/dashboard.html
git commit -m "feat(dashboard): комментарии и ответы постятся как anchored-чат; retire draft-комментариев (B1)"
```

---

## Task 6: B1 client — open-thread marker + header counter

**Files:**
- Modify: `templates/dashboard.html` (header markup ~line 469; CSS done in Task 4; `render()` tail ~line 877-882; helpers ~line 884)

Surface threads where the agent asked back and the human has not replied yet: a left-edge marker on the block plus a header counter that scrolls to the first open thread.

- [ ] **Step 1: Add the header counter element**

In the header, right before the chat-toggle button (`templates/dashboard.html:469`), add:

```html
    <button class="ghost" id="open-threads" hidden title="Перейти к ветке, ждущей ответа"></button>
```

- [ ] **Step 2: Add the open-thread helper**

Just above `function card(t, body){…}` (`templates/dashboard.html:884`), add:

```js
// An anchor's thread is "open" when the agent's latest needsAnswer turn has no
// human turn after it.
function openThreadAnchors(threadsByAnchor){
  const open = [];
  for(const a of Object.keys(threadsByAnchor||{})){
    const msgs = threadsByAnchor[a];
    let lastNeed = -1, lastHuman = -1;
    msgs.forEach((m, i) => {
      if(m.role === "agent" && m.needsAnswer) lastNeed = i;
      if(m.role === "human") lastHuman = i;
    });
    if(lastNeed >= 0 && lastHuman < lastNeed) open.push(a);
  }
  return open;
}
```

- [ ] **Step 3: Mark open blocks + update the counter at the end of `render()`**

In `render()`, replace the tail (`templates/dashboard.html:877-882`):

```js
  const __snap = captureActiveInput();
  $("#content").innerHTML = h || `<p class="empty">Агент ещё не наполнил дашборд.</p>`;
  wireBlocks();
  restoreActiveInput(__snap);
  highlightComments();
  updateQueue();
}
```

with (note `highlightComments()` already removed in Task 5):

```js
  const __snap = captureActiveInput();
  $("#content").innerHTML = h || `<p class="empty">Агент ещё не наполнил дашборд.</p>`;
  wireBlocks();
  restoreActiveInput(__snap);
  updateQueue();

  const openAnchors = openThreadAnchors(threadsByAnchor);
  const openSet = new Set(openAnchors);
  document.querySelectorAll("#content [data-anchor],#content [data-variant]").forEach(el => {
    const a = el.getAttribute("data-anchor") || el.getAttribute("data-variant");
    el.classList.toggle("thread-open", openSet.has(a));
  });
  const otBtn = $("#open-threads");
  if(otBtn){
    if(openAnchors.length){
      otBtn.hidden = false;
      otBtn.textContent = "🔸 " + openAnchors.length + " ждут ответа";
      otBtn.onclick = () => {
        const first = document.querySelector('#content .thread-open');
        if(first) first.scrollIntoView({ behavior:"smooth", block:"center" });
      };
    } else { otBtn.hidden = true; }
  }
}
```

- [ ] **Step 4: Manual verification**

Reuse the Task 4 fixture (it has an open thread: agent `needsAnswer` on `b1` with no later human reply):

```bash
mkdir -p .workflow/tasks/_scratch_b1
cat > .workflow/tasks/_scratch_b1/dashboard.json <<'JSON'
{"title":"Scratch B1","phase":"ELABORATE","status":"awaiting-batch","summary":"S.","planBlocks":[{"id":"b1","title":"Блок 1","body":"Тело."}]}
JSON
printf '%s\n' \
 '{"role":"human","text":"А почему так?","anchor":"b1","ts":"2026-06-19T12:00:00"}' \
 '{"role":"agent","text":"Уточни: какой формат?","anchor":"b1","needsAnswer":true,"ts":"2026-06-19T12:01:00"}' \
 > .workflow/tasks/_scratch_b1/chat.jsonl
python dev.py
```

Open `/?slug=_scratch_b1`. Expected: header shows `🔸 1 ждут ответа`; block **b1** has a warning left-edge marker; clicking the button scrolls to it. Append a human line to `chat.jsonl` (`{"role":"human","text":"json","anchor":"b1","ts":"2026-06-19T12:02:00"}`) → within ~5s the counter and marker clear. Then `rm -rf .workflow/tasks/_scratch_b1`.

- [ ] **Step 5: Commit**

```bash
git add templates/dashboard.html
git commit -m "feat(dashboard): маркер открытой ветки + счётчик «N ждут ответа» в шапке (B1)"
```

---

## Task 7: Orchestrator rules + knowledge base

**Files:**
- Modify: `skills/feature/dashboard-guide.md`, `skills/feature/state-schema.md`, `skills/feature/feedback-loop.md`
- Mirror (where the analogous file exists): `skills/{new-product,improve,ask}/dashboard-guide.md` and `…/feedback-loop.md`
- Modify: `docs/knowledge/areas/dashboard-feedback-ui.md`, `docs/knowledge/INDEX.md`

These are prose/contract docs (no code execution) — the agent's behavior change. Keep edits in the existing voice (Russian, matching surrounding text).

- [ ] **Step 1: Document `now`/`nowAt` in the dashboard schema**

In `skills/feature/dashboard-guide.md`, in "Field notes" after the **`progress`** bullet (`skills/feature/dashboard-guide.md:48`), add:

```markdown
- **`now`** / **`nowAt`** (optional): a one-line, human-readable description of what you are doing
  *right now* (`"исследую server.py"`, `"пишу тесты для /trace"`) and an ISO timestamp of when you
  set it. The header shows it as «Сейчас: …». Update `now` whenever your activity changes; bump
  `nowAt` with it. The page greys the line out after ~90s of staleness, so refresh it rather than
  letting it rot. Hidden while `status:"awaiting-batch"` (the «ждёт» badge takes over). Optional and
  back-compatible — omit it and the line simply hides.
```

- [ ] **Step 2: Note `now`/`nowAt` in `state-schema.md`**

In `skills/feature/state-schema.md`, in the **`workstreams[].status`** field note (`skills/feature/state-schema.md:39-40`), append a sentence:

```markdown
  Also reflect your current activity into `dashboard.json.now`/`nowAt` (a human one-liner) so the
  header's «Сейчас: …» stays live — see `dashboard-guide.md`.
```

- [ ] **Step 3: Document anchored chat + `needsAnswer` in `feedback-loop.md`**

In `skills/feature/feedback-loop.md`, in the «Chat» section (find it: `grep -n "chat.jsonl" skills/feature/feedback-loop.md`), add a subsection:

```markdown
### Anchored discussion (ветки на блоках)

A chat message may carry an **`anchor`** (a `planBlocks[].id`, the literal `summary`/`codebaseMap`,
or a demo variant `id`) and an optional **`quote`** (the fragment the human selected). The page renders
such messages as a **thread under that block/region/variant** instead of in the global chat panel.
This is now the channel for per-block discussion (it replaces the old draft-comment cards).

To reply in context, append your own line to `chat.jsonl` with the **same `anchor`**:

    {"role":"agent","text":"…","anchor":"b2","ts":"<iso>"}

When your reply is itself a **question back** to the human, add **`"needsAnswer": true`**. The page
then marks that block (left-edge warning) and raises a header counter «🔸 N ждут ответа» until the
human posts a later human turn on the same anchor. Drop `needsAnswer` (or omit it) for plain replies —
the page shows «✓ учтено агентом» when your reply follows a human turn. `needsAnswer` is agent-only;
the human's `POST /chat` never sets it.
```

- [ ] **Step 4: Mirror the rule into the other orchestrators**

Run `grep -rl "chat.jsonl" skills/{new-product,improve,ask}` and add the same two notes (`now`/`nowAt`; anchored chat + `needsAnswer`) to each skill's `dashboard-guide.md` / `feedback-loop.md` where those files exist, matching that skill's wording. (`/ask` and `/improve` ride the same contract per ADR-0013/0016, so the rule applies wherever the dashboard is written.)

- [ ] **Step 5: Update the knowledge base**

In `docs/knowledge/areas/dashboard-feedback-ui.md`, add a paragraph describing the new model: anchored chat threads replace draft-comment cards; `now`/`nowAt` header line; work-stream summary chip; `needsAnswer` open-thread marker + header counter; the single server change (`anchor`/`quote` passthrough in `_chat_post`). Then update its one-line entry in `docs/knowledge/INDEX.md` (line 16) to mention these, and bump the `_updated:` footer (`docs/knowledge/INDEX.md:39`).

- [ ] **Step 6: Sanity-check docs build/links**

Run: `python -m unittest discover -s tests`
Expected: OK — `tests/test_ask.py` has structural smoke checks over skill files; confirm nothing references a heading you renamed.

- [ ] **Step 7: Commit**

```bash
git add skills docs/knowledge
git commit -m "docs: правила now/nowAt и anchored-чат для оркестраторов + база знаний (A1/B1)"
```

---

## Self-review notes

- **Spec coverage:** A1 → Task 1; A2 → Task 2; B1 server (`anchor`/`quote`) → Task 3; anchored thread render → Task 4; comment→thread merge incl. demo-variant box + draft retirement → Task 5; counter-question marker + «N ждут ответа» + «учтено» → Task 6; orchestrator rules (`now`, `anchor`, `needsAnswer`) + KB → Task 7. Non-goals (C1/C2, pause/stop, phase map) intentionally absent.
- **`needsAnswer` source:** set only by the agent appending to `chat.jsonl` directly; the human `POST /chat` drops it (Task 3 test `test_needsanswer_from_human_is_ignored`). Consistent across Tasks 3/4/6/7.
- **Naming consistency:** `wsCounts`, `renderWsSummary`, `renderNow`, `nowAge`, `fmtAge`, `anchoredThreads`, `threadTurns`, `openThreadAnchors`, `postAnchored` — each defined once (near `card()`), referenced as written. `regionFooter(anchor, repliesByBlock, threadsByAnchor)` / `region(anchor, innerHtml, repliesByBlock, threadsByAnchor)` / `renderDemo(demo, repliesByBlock, threadsByAnchor)` signatures match all call sites updated in Task 4.
- **Back-compat:** every new field (`now`, `nowAt`, `anchor`, `quote`, `needsAnswer`) is optional; tasks without them render exactly as before. Legacy `replies.json` rendering is preserved in `regionFooter`.
- **Dead code:** Task 5 leaves `highlightComments`/`applyHighlights`/`wrapRange`/`cmtCard` unreferenced to keep the diff focused; a follow-up cleanup may delete them.
