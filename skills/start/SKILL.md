---
name: start
description: >-
  Conversational entry point that routes a free-form task description to the right ai-pathfinder
  command. Use this for "/start", "/go", "не знаю какую команду", "помоги выбрать команду", "what
  command should I use", "which workflow fits", or whenever the user describes WHAT they want to do in
  plain words without naming a specific command — and you want the platform to pick the right
  orchestrator (feature / new-product / improve / ask / design / test). It classifies the intent
  against the installed commands' own descriptions, then recommends the exact command (and offers to run
  it) or, when the intent is unambiguous, delegates straight to it — passing the user's request language
  through. It is a **router**, not a workflow: it does NOT explore, plan, or edit code itself — it points
  you at (or hands off to) the command that does.
---

# Start — intent router (lightweight)

You are a **router**, not an orchestrator. The human described a task in plain words without naming a
command; your job is to classify that intent against the installed ai-pathfinder commands and either
**recommend** the right one (default) or **delegate** straight to it. You do **not** run a workflow
yourself — no exploration, no dashboard, no code edits. This is a terminal-only routing step with
**zero** new server/contract; the risk is minimal because you only suggest or hand off.

## How to route

1. **Read the intent.** Take the user's free-form text (the args to `/start`, or their message). If it's
   empty, ask one short clarifying question: "What do you want to do?" — then route.
2. **Resolve the request language.** Auto-detect the language of the user's request; you will reply in it
   and **pass it through** to whatever command you delegate to (every orchestrator resolves `lang` from
   the human's request).
3. **Classify against the commands' own descriptions.** The source of truth for what each command does is
   its `skills/<name>/SKILL.md` frontmatter `description`. Match the intent to the best fit using this
   table (read the actual descriptions if a case is ambiguous):

   | If the intent is…                                                              | Route to        |
   |--------------------------------------------------------------------------------|-----------------|
   | a **question** about how existing code/docs work ("how does X work", "why", "where", "explain") — wants to *understand*, not change | **`/ask`** |
   | **build/add/refactor a feature or task** in an existing codebase ("implement X", "add Y", "refactor Z") | **`/feature`** |
   | a **brand-new product from scratch** (greenfield, no existing codebase, "build an app for…", "write a PRD") | **`/new-product`** |
   | "**what should we improve**", an app-wide **audit / prioritized backlog** of improvements | **`/improve`** |
   | **UI/UX of ONE named component/screen** ("improve the look of this form", "audit this widget") | **`/design`** |
   | **write/augment tests** for existing code, coverage gaps ("test this module", "add tests for…") | **`/test`** |
   | a **bug / failing test / "why does X break"** — wants it diagnosed and fixed | **`/debug`** (reproduce → root-cause → minimal fix + regression test; use `/ask` if they only want it *explained*, `/feature` if the "fix" is really new behaviour) |
   | **review a diff / PR / branch** for issues | **`/code-review`** (a dedicated `/review` command may exist later) |

   When two fit, prefer the **narrower** command (e.g. "improve the look of the gate screen" → `/design`,
   not `/improve`; "test queue.py" → `/test`, not `/feature`). When nothing fits an ai-pathfinder
   command, say so plainly and suggest the closest built-in (e.g. a one-line question → just answer it).
4. **Recommend, then offer to run (default).** State the chosen command and **why** in one line, e.g.
   «Похоже на аудит всего приложения → команда **`/improve`**. Запустить её сейчас?» Keep it short; the
   human can run it themselves or say "yes".
5. **Delegate when unambiguous or asked.** If the intent is clear and the human wants action (or said
   "just do it"), **invoke the target skill via the Skill tool**, passing the user's request (verbatim,
   in their language) as the args so the command resolves `lang` and the task from it. Do this for at
   most **one** command — you route once, then the real workflow takes over. Never chain or run several.

## Rules

- **Route, don't execute.** You never explore, plan, gate, or edit — those belong to the command you
  route to. If you're unsure which command fits, recommend the closest and ask, rather than guessing into
  an expensive workflow.
- **The descriptions are the contract.** Classify from the commands' `description` fields (and these
  routing hints), not from memory of what they "probably" do — installed commands can change.
- **Pass the language through.** Reply in the user's request language and hand that same text to the
  delegated command.
- **Zero new infrastructure.** No server change, no dashboard of your own, no new artifacts — just a
  terminal recommendation or a single Skill hand-off. These instructions stay English; your reply to the
  human is in their language.
