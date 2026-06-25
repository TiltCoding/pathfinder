# Phases — what to do in each one

Each phase ends by updating `state.json` (phase, iteration, checkpoint, findings, work-streams) and
`dashboard.json`. Spawn sub-agents with the Agent tool; pass them the slug and the absolute workspace
path. Keep the human's dashboard truthful at all times. The state machine is:

```
INTAKE → AUDIT → COMPOSE → CONSENT GATE → IMPLEMENT → VERIFY → DONE
```

## 1. INTAKE

Goal: capture **which one component** is under audit, locate it, and stand up the workspace.

- **Identify the component.** It is given by **name and/or screenshot** — handle all three cases:
  - **Only a name** (e.g. «форма настроек профиля», «верхний бар дашборда»): work from code. Use
    Grep/Glob to locate the component's source (`path:line`) and read it. Record what you found.
  - **Only a screenshot:** the human attaches an image on the dashboard (image-attachment contract,
    ADR-0020 — `POST /attach` → ref on a `/chat` line → file saved under `task_dir/attachments/`).
    `Read` the saved image file by its absolute path to get the visual context, then Grep/Glob to find
    the **matching code** for what's pictured.
  - **Both:** cross-check — the screenshot is the visual ground truth, the located code is what you'll
    actually edit. Note any mismatch (e.g. the screenshot shows a state the code can also reach).
  - If you genuinely cannot tell which component is meant, ask **one** clarifying question in chat
    before spawning the swarm — the whole workflow hinges on aiming at the right element.
- Write `brief.md` (from `templates/artifacts/brief.md` if present): the component, where it lives
  (`path:line`), how it was specified (name/screenshot/both), the attached screenshot reference if any,
  and any constraints the human gave. Keep it light.
- Create `state.json` (see `state-schema.md`) with `phase:"INTAKE"`, `iteration:0`,
  `component:{ name, location, screenshot? }`. In a git repo, record `baseCommit` = `git rev-parse HEAD`
  so the dashboard's **Changes** tab can diff the audit's work against the starting point.
- **Read the global language setting** from `~/.claude/ai-pathfinder/settings.json`
  (`{"lang":"en"|"ru"}`; graceful → `"en"` on any error/missing/unknown value). Record the resolved
  language in `state.json` as `lang`, and pass it to every sub-agent in its spawn prompt — it is the
  **default** output language for artifacts/dashboard (chat/reply channels still follow the human's
  message language).
- Start the companion server and copy the dashboard (see `feedback-loop.md`). Write the first
  `dashboard.json` (summary = the component + what `/design` will do, status `working`) and give the
  user the URL — invite them to attach a screenshot of the component there if they have one.
- The worktree was already stood up in SKILL step 1. Advance to AUDIT.

## 2. AUDIT (autonomous — the prism swarm)

Goal: critique the one component through every relevant UI/UX prism, then consolidate.

- **Spawn a swarm of `ds-auditor` agents in parallel**, **one per prism** (group two prisms into one
  agent only when they clearly overlap for this component — e.g. a static SVG icon has no real
  «движение» prism). The seven prisms:
  1. **визуальная иерархия и эстетика** — порядок чтения, контраст, типографика, ритм/отступы, баланс;
  2. **интеракция, фидбэк и аффордансы** — состояния (hover/focus/active/disabled), отклик на действие,
     понятность того, что кликабельно/вводимо, обработка ошибок и пустых состояний;
  3. **движение / микро-анимация** — переходы, спокойные индикаторы, уважение `prefers-reduced-motion`;
  4. **раскладка и адаптивность** — сетка, выравнивание, поведение на узких/широких экранах, перенос;
  5. **копирайт / ясность / микротексты** — лейблы, плейсхолдеры, кнопки, сообщения — тон и
     однозначность;
  6. **доступность (a11y)** — семантика/роли, метки, контраст, фокус-видимость, навигация с клавиатуры,
     поддержка скринридеров;
  7. **логика потока / информационная архитектура** — порядок шагов, группировка, лишние/недостающие
     поля, ведёт ли компонент пользователя к цели.
- Brief each auditor with: the component's code (`path:line`), the screenshot reference (so it can
  `Read` the image too — give the absolute path), the convention that it **reads `docs/knowledge/INDEX.md`
  first** and respects the app's existing design tokens/components, and its single prism. Each auditor
  is **read-only** (no Write/Edit) and returns **structured findings**, one block per issue:

  ```
  ### finding
  prism: <its prism>
  severity: high | med | low
  problem: <what's wrong, concretely, grounded in the component>
  location: <path:line of the element to change>
  proposal: <the specific fix — token/markup/copy/aria change, fitting the existing system>
  ```

- **Consolidate (orchestrator-only).** Collect every auditor's findings and **dedup** them: two prisms
  often flag the same element (e.g. a low-contrast CTA is both «эстетика» and «a11y») — merge those into
  one finding that keeps both prism tags. Mint a **stable `id = f<k>`** (`f1`, `f2`, …) per consolidated
  finding, **rank** them (severity first, then how central the element is to the component's job), and
  write the ranked list to `findings.md`. Auditors never consolidate and never spawn sub-agents — the
  synthesis is yours.
- Update `dashboard.json` (status `working`, a short note that the audit is consolidated) and advance to
  COMPOSE.

## 3. COMPOSE (orchestrator builds the demo)

Goal: turn the consolidated findings into **one self-contained annotated demo** the human can read at a
glance.

- Build **one** file `mockups/redesign.html` — a single self-contained mockup that shows the
  **redesigned** component with **all** findings applied, annotated in **Variant A** style:
  - **Numbered badges ①②③…** overlaid on the redesigned component, one per finding, positioned over the
    element each finding touches; the badge number matches the finding's `f<k>` order (badge 1 → `f1`).
  - **A side legend** — one row per finding: the number → the problem (short) → **what changed** (the
    fix), with a small prism tag chip. This is the "number → problem → what changed" mapping.
  - **A «До/После» toggle** — a control that flips the mockup between the original look ("До", badges and
    after-only affordances hidden) and the redesigned look ("После", default). The exemplar uses a
    `body.before` class toggled by two buttons; markers and new states live in `.only-after` / are hidden
    under `body.before`.
- **Self-contained under CSP:** inline `<style>`/`<script>` only, **no CDN or any network request**;
  `data:` URI images are fine. It renders read-only in a sandboxed iframe via `GET /mockup`, so it must
  stand alone. Match the app's dark-dashboard palette/tokens where it makes sense so the redesign reads
  as part of the system.
- A faithful **exemplar** of this exact format ships alongside this skill at
  `skills/design/redesign.example.html` (Variant A — badges ①–④ + legend + «До/После» toggle). **Copy it
  as the starting point** for `mockups/redesign.html` and reproduce its structure: a `.stage` holding the
  component with absolutely-positioned `.mark` badges, an `aside.legend` with one `.leg` row per finding,
  and the toggle in a `.toolbar`. Adapt the count and content to **this** run's findings.
- Render `dashboard.json.demo` as a **single** variant (see `dashboard-guide.md`):

  ```json
  "demo": {
    "kind": "ui",
    "intro": "Markdown — один макет на все находки: бейджи ①②③ + легенда + тогл «До/После».",
    "selectionId": "design-demo",
    "variants": [
      { "id": "redesign", "title": "Предлагаемый редизайн (аннотированный)", "file": "redesign.html",
        "caption": "Markdown — что показано и как читать бейджи/легенду." }
    ]
  }
  ```

  There is **no** pick-one selection here (one variant), so the human doesn't choose between mockups —
  the per-finding choice happens at the CONSENT GATE next. Advance to the CONSENT GATE.

## 4. CONSENT GATE (the one human gate)

Goal: let the human pick **which findings to apply**. This is the single decision point; an iteration
loop driven by batched feedback.

- Render **each consolidated finding as a `planBlocks[]` ↔ `questions[]` pair** sharing the **same
  `id = f<k>`** (feat-K style, ADR-0013):
  - `planBlocks[f<k>]` — a card: «N. <короткое имя находки>» with the problem, the proposal, the prism
    tag(s), severity, and `location` (`path:line`). Write it so it's worth reading next to badge N in the
    demo.
  - `questions[f<k>]` — `kind:"choice"`, `options:["Применить","Пропустить"]`, **default «Применить»**
    (the audit's recommendation is to apply; the human unchecks what they don't want).
- Set `dashboard.json` status to `awaiting-batch`. Tell the human briefly in chat that the demo + the
  per-finding checklist are ready: uncheck any finding they don't want, click **«Отправить»** (Submit),
  then **«Утвердить план»** (Approve = "implement the remaining set"). Full contract:
  `dashboard-guide.md` §CONSENT GATE.
- Park at the checkpoint and wait (see `feedback-loop.md`). When a new `submissions/<n>.json` appears:
  read every item (a finding's `answer` of «Пропустить», a comment on a finding card or on the demo
  variant), record each finding's decision in `state.json.findings[]`, revise the finding/demo if a
  comment asks for it, and write a short `replies.json` entry per item (keyed to the `f<k>`/variant id)
  so the human sees the change. Bump `iteration`, refresh `dashboard.json`, park again.
- **Mandatory order Submit → Approve.** `draft.json` is **not** readable over HTTP, so the picks are
  only visible after Submit. If an `approve-plan` signal arrives with no fresh `submissions/<n>.json`,
  there's nothing to read — ask the human to click «Отправить» first. **Default «нет ответа =
  Применить»** here (the inverse of `/improve`, because the gate is "опт-аут", not "опт-ин"): a finding
  the human never touched stays «Применить».
- Repeat until **«Утвердить план»** (an `approve-plan` signal). Then freeze the **approved set** = every
  finding whose decision is «Применить», finalize `workstreams[]` in `state.json` (one per approved
  finding, or a grouped stream of related findings — each with an id/title/status `todo`), and advance to
  IMPLEMENT. In headless/eval mode: skip waiting, auto-apply any pre-seeded submissions or take all
  findings, auto-approve.

## 5. IMPLEMENT (autonomous)

Goal: apply only the approved findings.

- For each approved finding (or a grouped work-stream of related findings touching the same area) spawn a
  `ds-coder` agent with Write/Edit. **The plan is the finding itself** — its `proposal` + `location` —
  so the coder just applies it; give it the finding, the redesign demo as the visual target, the
  component's code, and the convention that it must **read `docs/knowledge/`** and match existing design
  tokens/components/style (reuse, not a parallel system). Independent streams run in parallel; long ones
  use `run_in_background`.
- **No `wf-documenter` run** — `/design` is small. The orchestrator documents lightly (see DONE).
- As streams complete, mark them `done` in `state.json` and `dashboard.json` and update `progress`.
  Reflect the current activity into `dashboard.json.now`/`nowAt` so the «Сейчас: …» header stays live.
- Between work-streams you hit checkpoints: if the human sent chat messages (`chat.jsonl`, see
  `feedback-loop.md`), consume them (answer in chat, adjust remaining streams) before continuing. Chat
  never interrupts a running coder — it's handled at the next checkpoint.

## 6. VERIFY (autonomous)

Goal: confirm the redesign actually works for the touched component.

- Spawn `wf-reviewer` (reused from the feature workflow): run the project's tests/linters/build for the
  touched component, review the diff for correctness, and report findings. Fix or spawn a `ds-coder` to
  fix real issues; re-run until green or until you have a clear blocker to surface.
- **Review gate (auto):** after `wf-reviewer` is green, run the `/code-review` skill over the diff as a
  gate, exactly like the feature workflow. Capture the run into `reviews.json` (shape in
  `feedback-loop.md`): `status:"running"` before you invoke it, rewritten to `done`/`failed` with a short
  `summary` and ranked `findings` when it returns. The **«Изменения»** tab renders these next to the
  change diff. Treat high-severity findings as fix-or-justify before DONE. A human can request a re-run
  via the **`run-code-review`** signal — honor it at your next checkpoint.
- Update `dashboard.json` with verification status.

## 7. DONE

- Write a final summary into `dashboard.json` (which findings were applied vs skipped, what changed,
  where — clickable `path:line` — and how it was verified) and set status appropriately. Set
  `phase:"DONE"` in `state.json`.
- **Document lightly:** append a one-line entry to `docs/knowledge/task-log.md` (what component, which
  findings applied). Add an ADR only if the redesign carried a genuinely notable decision — usually it
  doesn't. No `wf-documenter` run and no INDEX/area rewrite for a routine component audit.
- Tell the user what landed and point at the dashboard and the diff in the **«Изменения»** tab.
