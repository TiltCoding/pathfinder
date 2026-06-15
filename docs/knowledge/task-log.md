# Журнал задач

> Append-only. Каждая задача воркфлоу оставляет запись: что и **зачем** изменено. История для будущих агентов.

<!-- Новые записи — сверху. -->

## 2026-06-15 — dashboard-light-dark-theme (v0.14.0)
- **Что:** Дашборд (`templates/dashboard.html`) и хаб (`HUB_PAGE` в `scripts/server.py`) получили
  **пользовательское переключение темы** Светлая / Тёмная / Системная.
  - **Модель:** `localStorage['theme']` хранит предпочтение `'light'|'dark'|'system'` (дефолт
    `'system'`); на `documentElement` ставится **разрешённый** `data-theme="light|dark"` (никогда
    `'system'`); `'system'` резолвится через `matchMedia('(prefers-color-scheme: dark)')` + `change`.
  - **Триггер сменён с media на атрибут:** 5 блоков `@media (prefers-color-scheme: dark)` переписаны на
    `:root[data-theme="dark"]`, светлый `:root` → `:root[data-theme="light"]`.
  - **FOUC-bootstrap:** инлайн-`<script>` в `<head>` перед `<style>` (`templates/dashboard.html:7-21`,
    аналог `HUB_PAGE` `:1237-1253`) применяет тему до первой отрисовки; резолв продублирован.
  - **Контрол:** segmented `.seg#theme-seg` ☀️🌙🖥 в шапке рядом с «Чат» (`:460-464`), реюз паттерна
    `.seg` (ADR-0005); на узкой ширине подписи скрываются.
  - **Токены ошибки:** введены `--err`/`--err-soft` в обе темы (в тёмной красный светлее, `#f87171`),
    заменили ~11 повторов `#ef4444`; `.status.awaiting` → soft-токен варнинга.
  - **Хаб `/hub`** темизирован тем же подходом и **тем же ключом** `localStorage['theme']` → сквозной
    выбор темы хаб↔задача; палитры хаба и дашборда синхронизированы.
- **Зачем:** тёмная тема существовала только как системный media-оверрайд — пользователь не мог
  выбрать тему. Тема — чисто клиентская (сервер/контракты не тронуты).
- **Ключевые решения:** переключение через `data-theme`-атрибут + localStorage-предпочтение + резолв
  `'system'` через matchMedia (vs оставить media / класс на body / серверная тема — отвергнуты).
  Следствие: тёмный блок **не наследует** светлый → полнота тёмной палитры обязательна (добавлены ранее
  не-переопределявшиеся токены); FOUC-bootstrap дублирует резолв; общий ключ для хаба и дашборда; именная
  шероховатость `--warn-soft` (дашборд) vs `--awaiting-soft` (хаб) — **ADR-0015**.
- **Не тронуто (by design):** ролевая палитра таймлайна `ROLE_COLORS`/`roleColor` (ADR-0011), песочные
  `iframe.mock`, `color:#fff` на цветных подложках.
- **План:** `.workflow/tasks/dashboard-light-dark-theme/plan.md`
- **ADR:** `decisions/ADR-0015-theme-toggle-data-attribute-localstorage.md`
- **Область:** `areas/dashboard-theming.md`

## 2026-06-13 — improve: DISPATCH → последовательная очередь `/feature` (v0.13.0)
- **Что:** Переделана модель DISPATCH у `/improve`. Было — **посев каждой выбранной фичи в свой git
  worktree + ручной запуск** человеком (seed-and-handoff). Стало — **очередь
  `.workflow/dispatch-queue.json` + последовательный дренаж через `/feature`, каждая фича в свежем
  контексте**. См. [ADR-0014](decisions/ADR-0014-improve-sequential-feature-queue.md).
- **Зачем:** на реальном прогоне `improve-runtime` (8 пиков) выяснилось, что запускать 8 worktree
  вручную невозможно, а «одна сессия делает все 8» раздувает контекст. Очередь + ре-инвок `/feature`
  даёт полное качество `/feature` на каждой фиче при чистом контексте.
- **Как:** `/improve` DISPATCH пишет только `brief.md` + `pending`-item в очередь (без worktree, без
  per-feature state/dashboard, без запуска `/feature` из своей сессии). `/feature` получил **queue-mode**:
  без явной задачи берёт младший `pending`, бриф уже готов (пропуск INTAKE), гонит полный workflow, на
  DONE помечает `done` и просит `/clear` + `/feature` (или `/loop /feature`).
- **Файлы:** `skills/improve/{SKILL,phases,consensus,state-schema}.md`, новый
  `skills/improve/dispatch-queue.md` (контракт очереди), `skills/feature/{SKILL,phases}.md`,
  `.claude-plugin/plugin.json` (0.12.0→0.13.0). Решение выбора `feat-K` и агрегация (ADR-0012/0013) —
  без изменений. Параллельный worktree-фан-аут остаётся opt-in (ADR-0010).

## 2026-06-13 — improve-workflow
- **Что:** Добавлена третья команда-оркестратор **`/improve`** — производитель feature-прогонов (не
  редактор кода). Стадии `INTAKE → SCOUT → CONSENSUS → PROPOSE/SELECT GATE → DISPATCH → DONE`: рой
  аналитиков обследует приложение → консенсус голосующей панели → человек выбирает фичи → посев
  параллельных `/feature`-прогонов в git-worktree (seed-and-handoff).
  - **Скилл `skills/improve/`** (ws1): `SKILL.md` (frontmatter с `description`, разведённым от
    `/feature`/`/new-product`; mental model; таблица под-агентов; start/resume; operating rules;
    телеметрия), доменные `phases.md` (машина стадий) и `consensus.md` (рой → дедуп → vote-панель →
    детерминированная агрегация → seed-and-handoff). Переносимые копии из `skills/feature/`:
    `feedback-loop.md`/`parallel.md`/`knowledge-guide.md` — почти дословно; `state-schema.md`
    (+секция «improve-specific fields») и `dashboard-guide.md` (+секция «SELECT GATE — контракт `feat-K`»).
  - **Под-агент `agents/wf-improver.md`** (ws2): read-only (`tools: Read, Grep, Glob, Bash`, без `model:`),
    **два режима** scout/vote, различаемые **промптом** оркестратора. SCOUT — кандидаты по призме (схема
    `### cand:`); VOTE — независимая оценка всего списка 0–3 (`### cand-K`). Один файл на два режима —
    следствие «model глобальна для subagent_type».
  - **SELECT GATE + DISPATCH** (ws3): выбор фич через контракт `feat-K` (карточка `planBlocks` ↔
    `questions[choice]` с тем же id, `options:["Делаем","Пропускаем"]`) + сигнал `approve-plan`, **0 правок
    `server.py`/`dashboard.html`**; DISPATCH реюзит `scripts/worktree.py` и хаб (`/hub`) без правок.
    (Тест `tests/test_improve_dispatch.py`, eval-кейс, рубрика `evals/rubrics/improve-quality.md` и бамп
    `plugin.json` 0.11.0 → **0.12.0** пишет поток ws3 — задокументировано здесь по плану b6.)
- **Зачем:** третий сценарий плагина — превратить «надо что-то улучшить» в приоритизированный,
  выбранный человеком набор feature-задач. «Консенсус роя» реализован честно: независимые голоса панели +
  детерминированная агрегация оркестратором (субагенты не спавнят субагентов; один LLM-агрегатор был бы
  невозможен и нечестен). Главный гейт — поверх существующего контракта (0 правок сервера/HTML). Реальный
  автозапуск N независимых Claude Code-сессий недоступен → seed-and-handoff: оркестратор готовит почву,
  человек заходит в каждый worktree и запускает `/feature`.
- **Ключевые решения:**
  - **Механика консенсуса** — рой 7 scout по призмам → консолидация+дедуп оркестратором (`cand-1…N`) →
    панель 3 vote → детерминированная агрегация (`score=(mean(imp)−w_e·mean(eff)−w_r·mean(rsk))·mean(conf)/3`,
    дефолты `w=0.5`, «согласие»=доля keep, топ-K 6–8). Почему панель + арифметика оркестратора, а не
    «консенсус в одном агенте» — **ADR-0012** (расширяет паттерн судейской панели ADR-0006/0007).
  - **Reuse-first выбор фич / 0 правок сервера** — контракт `feat-K` через `questions[choice]`+`approve-plan`,
    дефолт «нет ответа=Пропускаем», обязательный порядок Submit→Approve (`draft.json` не READABLE).
    Развилка A (0 правок, выбран) vs B (аддитивный чеклист в HTML, оставлен как апгрейд) — **ADR-0013**
    (связь с ADR-0008).
- **Утверждённые параметры (зафиксированы):** Вариант A для гейта (q1); 7 призм scout / 3 голосующих /
  топ-K 6–8 (q2); посев в `EXPLORE`/`working` (q3); одна модель `wf-improver` на оба режима (q4); дефолт
  «нет ответа = Пропускаем» (q5); лёгкая eval-фикстура (q6).
- **План:** `.workflow/tasks/improve-workflow/plan.md`
- **ADR:** `decisions/ADR-0012-improve-consensus-panel-deterministic-aggregation.md`,
  `decisions/ADR-0013-improve-feature-pick-reuse-zero-server.md`
- **Область:** `areas/orchestrator-skills.md` (расширена секцией про `/improve`)

## 2026-06-13 — rework-agent-timeline
- **Что:** Переписан таймлайн параллелизма сабагентов на вкладке «Трейсинг» под **Вариант C** —
  целиком на клиенте, в `renderGantt(host, subs, tr)` (`templates/dashboard.html`), **без правок сервера**.
  - **Две зоны в одном `section.card`:** (1) обзор всего прогона `[T0..T1]` — inline-`<svg>`
    **token-rate area chart** (высота = темп производства токенов, не число агентов) + полоса фаз +
    ось `HH:MM` UTC с делениями + **brush** для выбора окна; (2) детальная зона выбранного окна
    `[bs..be]` — greedy lane-packing, бары-`<div>` в пикселях («вместить окно в ширину карты»),
    цвет = роль, фон = регионы фаз.
  - **Фикс корневого бага палитры (b1):** новые хелперы `roleKey`/`roleLabel` рядом с
    `roleColor`/`ROLE_COLORS` — роль приходит с namespace-префиксом `ai-pathfinder:wf-*` и
    промахивалась мимо словаря (почти все бары были хеш-цветными). `roleKey` срезает префикс и `wf-`.
  - **Фазы (b14):** `phase` из `subs[]` рисуется как фоновые регионы + полосы-заголовки (фон=фаза,
    цвет бара=роль), бакет «вне фазы» для `null`.
  - **Поля `/trace`, использованные таймлайном:** `startTs/endTs`, `durationMs`, `out`, `phase`,
    `role`, `summary`, `spanId`, `ok` (+ `model`/`contextPct`/`costUsd` в тултип); границы окна — из
    готового `tr.timeline.{t0,t1}`. Сервер (`build_trace`/`_agent_record`/`scripts/server.py`) не тронут.
- **Зачем:** на длинном прогоне (десятки агентов × часы) старый гант не читался — бары сжаты в ширину
  карты, ось из двух меток, `fmtDur` без часов, палитра промахивалась. Вариант C даёт обзор-целиком +
  зум окном без горизонтального скролла; обзор по токенам честнее показывает «где сожгли работу».
- **Ключевые решения:** обзор по производству токенов (`rate=out/durationMs`, агрегация по бакетам —
  аппроксимация, т.к. `out` — итог, не временной ряд); разбивка на фазы (с честной оговоркой о
  разрежённости `phase`: 26/36 `null` в тест-датасете, т.к. фаза = `state.json.phase` на момент
  `subagent.start`; `workstream` хук не пишет вообще); нормализация `roleKey`; подводный камень
  `sig`-гарда (интерактив/brush — вне summary-DOM) — **ADR-0011**.
- **Доки:** обновлён `areas/telemetry-tracing.md` (раздел «Таймлайн «Трейсинг» — Вариант C»: поля
  `/trace`, нормализация роли, `sig`-гард). 0 правок сервера → доки серверного контракта не трогались.
- **План:** `.workflow/tasks/rework-agent-timeline/plan.md`
- **ADR:** `decisions/ADR-0011-timeline-variant-c-token-rate-phases.md`
- **Область:** `areas/telemetry-tracing.md`
- **Статус финала:** ✅ DONE. Реализовано и проверено в VERIFY. Фактические хелперы рендера:
  `drawOverview()`/`drawDetail()` (две зоны), `bindBrush()` (drag→окно, dblclick→весь прогон),
  `tokTotalWin()` (Σ токенов окна), `tickStepMs()` (шаг оси), `roleKey()`/`roleLabel()`,
  `fmtDurH()`/`clock()`, `PHASE_META`/`PHASE_ORDER`/`PHASE_NONE`; новый стабильный контейнер
  `#trace-gantt` (в `ensureTraceShell`), вынесенный из `#trace-summary`, чтобы 4-сек тик не сбивал
  brush; тултип — `#g-tip` (`position:fixed`, делегаты биндятся один раз). Проверка: тесты 26/26,
  `node --check` шаблона OK, XSS-аудит чист, живой рендер на 42 агентах/4.4 ч (обе темы), brush зумит
  и переживает перерисовку. `/code-review` (medium) — 0 подтверждённых багов.

## 2026-06-13 — parallel-runs-hub
- **Что:** Параллельные `/feature`-задачи в git worktree + хаб всех запусков.
  - **CLI `scripts/worktree.py` (новый, b1).** Подкоманды `add`/`list`/`remove` (stdlib-only):
    `add <slug>` создаёт worktree `../pathfinder-worktrees/<slug>/` на ветке `<slug>` от `main`,
    симлинкует `<worktree>/.workflow → <main>/.workflow` (`_ensure_workflow_symlink`) и пишет
    `worktreePath`/`branch` в `state.json` (`record_worktree_in_state` — чистая, тестируемая без git).
    Идемпотентен (resume переиспользует worktree/ветку). `main_root` через
    `git rev-parse --git-common-dir` работает и из worktree. `remove` чистит worktree+симлинк, но
    **не** трогает `.workflow/tasks/<slug>/` (история остаётся).
  - **Per-worktree diff (b6).** `_git(*args, cwd=None)` (`scripts/server.py:537`) и новый
    `_task_root(slug)` (`:550`): вкладка «Изменения» диффит `git -C state.worktreePath` (валидируется),
    fallback на main без поля. Прокинуто в `_build_changes`/`_changes_file`/`_base_commit`/`_is_noise`.
  - **Хаб (b5/b7/b8/b9/b10).** `GET /hub.json` (`_hub`/`_build_hub`/`_hub_run`, `scripts/server.py:706`)
    — кросс-задачный агрегат `{runs, analytics}` по `_list_tasks()`, кэш+лок+мягкая деградация,
    read-only. `_hub_telemetry` — **один проход** `telemetry.jsonl` (без транскриптов/`build_trace`).
    Критерий active/history `_hub_is_active` (q7): `phase ∉ {DONE,ABORTED}` И `updatedAt`<24ч
    (`HUB_ACTIVE_WINDOW_SEC`/`HUB_TERMINAL_PHASES`). `GET /hub` (`HUB_PAGE`) — инлайн-HTML без CDN, три
    секции Активные/История/Аналитика (вариант A), поллинг 3 c; ссылка на хаб в `INDEX_LANDING`.
  - **Per-session `active.json` (b3).** `active_slug(root, session_id)` (`scripts/_aipf.py:94`) сначала
    читает `.workflow/active/<session_id>.json` (`SESSION_ID_RE`, анти-traversal), затем `active.json`,
    затем свежайший `state.json`. Без per-session файла — старое поведение.
  - **Скилл/схема (b2/b4):** новый `skills/feature/parallel.md`, поля `worktreePath`/`branch` и
    per-session файл описаны в `skills/feature/state-schema.md`.
- **Зачем:** `.workflow/` gitignored → в worktree локален и пуст → хаб не видел бы чужие задачи.
  Симлинк сводит артефакты всех worktree в ОДИН store, который читает единственный сервер; `worktreePath`
  в state даёт серверу рабочее дерево для diff; per-session `active.json` чинит атрибуцию при общем
  store. Аналитика событийная (без токенов): транскрипты дороги и физически отсутствуют в worktree.
- **Ключевые решения:** симлинк (vs env vs реестр), `worktreePath` в state дозаписью, per-session
  `active.json`, токены вне кросс-задачного агрегата, отдельная страница `/hub` (не вкладка) — ADR-0010.
- **Дрейф доков:** заодно задокументировано недокументированное событие `phase` (пишет оркестратор, не
  хук; форвардится в Langfuse веткой `phase`/`gate`) в `areas/telemetry-tracing.md`.
- **План:** `.workflow/tasks/parallel-runs-hub/plan.md`
- **ADR:** `decisions/ADR-0010-shared-store-symlink-worktree.md`
- **Область:** `areas/parallel-runs-hub.md`

## 2026-06-13 — agent-trace-details
- **Что:** Вкладка «Трейсинг» детализирована по агентам. Бэкенд (`scripts/telemetry_hook.py`,
  `scripts/_aipf.py`, `scripts/server.py`; новый `tests/test_telemetry_actions.py`):
  - **MCP в ленте (b1).** В `tool.*`-ветку `build_event` (`scripts/telemetry_hook.py:134`) добавлен захват
    `mcp__*` той же механикой (`spanId="tool-"+toolUseId`). В `tool.start` дозаписаны НОВЫЕ поля: `kind`
    (`mcp`/`bash`/`tool`), для MCP ещё `server`/`mcpTool` (`_parse_mcp_name`, разделитель `__`; `arg` —
    первое строковое значение input, обрезка 200). Старый формат/порядок `tool.*` не тронут.
  - **Описание задачи в `/trace` (b2).** `_agent_record(..., summary=...)` (`scripts/_aipf.py:776`) кладёт
    `summary` в запись агента; под-агент — `subagent.start.summary`, оркестратор — авто-подпись
    `"оркестратор сессии"` (q5=A).
  - **Ленивый `GET /trace/actions?slug&agent&session` (b3).** `_trace_actions` (`scripts/server.py:394`),
    читалка `parse_transcript_actions` (`scripts/_aipf.py:451`), мост спан→транскрипт через sidecar
    `agent-*.meta.json` (`find_subagent_meta` `:263`, `_agent_description` `scripts/server.py:439`). Читает
    ОДИН транскрипт по раскрытию, read-only, mtime-кэш ~3 с. Контракт `{description, actions, counts,
    pending}`, идёт по ВСЕМ session_id задачи.
  - **Фронтенд (b4/b5)** правился параллельно: посмертная карточка агента стала раскрываемой (описание +
    две ленивые секции «Действия»/«Сообщения агента», единая хронология v2), живая лента типизирует
    MCP-строки `server · tool`.
- **Зачем:** lane из хука best-effort и не даёт достоверной по-агентной сводки (исполнителя нет в payload).
  Exploration вскрыл дрейф знаний: sidecar `meta.json.toolUseId` == `spanId` под-агента даёт
  **детерминированный** мост спан→транскрипт→его `tool_use` (вкл. MCP) в обход lane; у одной задачи бывает
  несколько session_id (поправка к ADR-0003). Достоверный список вынесен в ленивый эндпоинт, чтобы не
  грузить горячий путь ленты (ADR-0001) и не утяжелять `build_trace`.
- **Ключевое решение:** точная атрибуция через транскрипт + `meta.json`, новый ленивый `/trace/actions`
  вместо расширения `/trace`, MCP — дозаписью полей `kind/server/mcpTool` (ADR-0009, поправка к ADR-0003).
- **План:** `.workflow/tasks/agent-trace-details/plan.md`
- **ADR:** `decisions/ADR-0009-transcript-attribution-and-actions-endpoint.md`
- **Область:** `areas/telemetry-tracing.md`

## 2026-06-13 — dashboard-feedback-enhancements
- **Что:** Во вкладку «Контент» дашборда (`templates/dashboard.html`) добавлены две возможности
  обратной связи **без правок `scripts/server.py`**:
  - **Свой ответ на `choice`-вопрос** — под radio-опциями всегда видимое поле `<textarea data-answer>`
    (`render()` ветка choice, `:670`/`:677`). Приходит как обычный `answer` того же `questionId`;
    свой ответ перебивает выбор опции и наоборот (`wireBlocks` `:760`/`:766`), один `answer` на вопрос
    (серверный дедуп). `answer.text` может не совпадать ни с одной `options` — это свободный ответ.
  - **Коммент к демо-варианту** — у каждого `demo.variants[]` всегда видимое поле → `comment` с
    `blockId = vr.id`, `selectedText:""` (`saveVariantComment` `:898`). `regionFooter(vr.id)` теперь
    рендерится один раз на вариант (в т.ч. без `caption`, `renderDemo` `:746`), поэтому реплаи агента
    по `blockId===vr.id` видны всегда.
  - Синхронизирована документация скилла: `skills/feature/dashboard-guide.md`,
    `skills/feature/feedback-loop.md` (контракт чтения submission для агента).
- **Зачем:** закрыть два разрыва в обратной связи (нельзя было ответить своей формулировкой; нельзя
  было прокомментировать вариант без caption — и реплаи к такому варианту были невидимы). Backend
  агностичен к содержимому items, поэтому обе фичи — чистый фронтенд + синк доков скилла.
- **Ключевое решение:** реюз существующего draft-контракта (`answer` вне `options`; `comment` с
  `blockId=vr.id`) вместо новых полей/флагов/эндпоинтов → 0 правок сервера (ADR-0008).
- **План:** `.workflow/tasks/dashboard-feedback-enhancements/plan.md`
- **ADR:** `decisions/ADR-0008-feedback-on-existing-contract-zero-server.md`
- **Область:** `areas/dashboard-feedback-ui.md`

## 2026-06-12 — new-product-workflow
- **Что:** Добавлена команда-оркестратор `/new-product` — создание продукта с нуля (greenfield).
  Стадии `INTAKE → DISCOVER → PRD → PRD-GATE → PHASE-PLAN → PLAN-GATE → BUILD → SHIP → DONE` с
  эволюционным build-loop (generate → tests → judge → refine) на каждую фазу продукта.
  - Скилл `skills/new-product/` (SKILL.md, phases.md, **loop.md**, feedback-loop.md, state-schema.md,
    dashboard-guide.md, knowledge-guide.md) — зеркало `/feature` + greenfield-стадии и спека цикла.
  - Ростер `agents/np-*.md` с пиннингом модели во frontmatter: `np-thinker` (`model: fable`, tools
    урезаны до Read/Write/Edit — структурно не читает сырьё), `np-researcher`/`np-coder`/`np-judge`
    (`model: opus`). Реюз `wf-reviewer`/`wf-documenter`.
  - Шаблоны `templates/artifacts/{prd,phase-plan,judge-verdict,iteration-scratchpad,research-digest}.md`.
  - Инфраструктура (сервер/дашборд/телеметрия) не тронута: PRD/фазы → `planBlocks`/`workstreams`,
    вердикты судьи → `reviews.json` (`kind:"judge"`); greenfield-дифф — через empty-tree `baseCommit`
    `4b825dc6…`. README, `plugin.json` (0.7.0→0.8.0), `marketplace.json`, eval-фикстура
    `evals/fixtures/greenfield-mini/`.
- **Зачем:** второй сценарий плагина — создание продукта с нуля с самоулучшающимся циклом (судья +
  тесты) при фиксированной маршрутизации моделей (fable — мыслитель на выжимках; opus —
  исследование/реализация/судейство). «Исследователь кормит мыслителя» реализовано через оркестратора
  (субагенты не спавнят субагентов).
- **Гейт-решения:** гибридный гейт (тесты — стена, судья — руль), вердикт-объект вместо pass/fail,
  заморозка PRD-производных тестов (анти-гейминг), Reflexion-scratchpad, 3 стоп-условия + эскалация,
  гейт-политика V1 (два гейта).
- **План:** `.workflow/tasks/new-product-workflow/plan.md`
- **ADR:** `decisions/ADR-0006-np-agent-roster-model-pinning.md`,
  `decisions/ADR-0007-evolutionary-build-loop.md`
- **Область:** `areas/orchestrator-skills.md`

## 2026-06-10 — changed-files-tree-view
- **Что:** Вкладка «Изменения» дашборда переписана: дерево файлов, только реально изменённые файлы,
  подсветка синтаксиса в diff.
  - Backend `scripts/server.py`: `_git` форсирует `encoding="utf-8", errors="replace"` (`:446`);
    `_build_changes` добавляет `-c core.quotePath=false` в `diff --numstat` (`:489`) и
    `status --porcelain --untracked-files=all` (`:511`) — честные UTF-8-имена + развёрнутые untracked-
    каталоги; renames в numstat-ветке пропускаются (`old => new`); новый `_is_noise` (`:546`) прячет
    0-байтные untracked; в запись файла добавлено поле `untracked` для фронтового тумблера.
  - Frontend `templates/dashboard.html`: `langFromPath` (`:520`) + построчный `highlightCode` (`:540`)
    без CDN; `buildFileTree` (`:961`) строит дерево из плоского `files` на фронте; `renderChangeTree`
    (`:991`) + `toggleChangeDir` (`:1008`) рисуют/сворачивают дерево; `renderDiff(text, lang)` (`:1020`)
    подсвечивает тело строки поверх add/del/hunk; CSS токенов `.tok-*` (`:261`) и segmented-тумблер
    «Только tracked / Все».
- **Зачем:** вернуть честный, читаемый список изменений (кириллица, развёрнутый `docs/`, без 0-байтного
  мусора) и «код как код» в diff, не ломая кеш 2 с/лок, traversal-guard, мягкую деградацию и контракт
  `_build_changes` с knowledge-графом (пометка touched).
- **Ключевые решения:** фильтр 0-байтного мусора + тумблер (ADR-0005); `core.quotePath=false`+`-uall`
  для честных имён и разворачивания каталогов; встроенный токенайзер без внешней сети (ADR-0004); дерево
  строится на фронте (backend почти нетронут).
- **Как проверено:** AST-разбор `server.py`, `node --check` для `dashboard.html`, живой `/changes` (нет
  `\320…`-имён, есть `docs/...`, нет 0-байтных stray в режиме «только tracked»), traversal-guard
  (`file=../..` → not found), отсутствие XSS (подсветка поверх `esc()`). Ревью зелёное.
- **План:** `.workflow/tasks/changed-files-tree-view/plan.md`
- **ADR:** `decisions/ADR-0004-inline-syntax-highlight-no-cdn.md`,
  `decisions/ADR-0005-untracked-noise-filter-zero-byte-toggle.md`

## 2026-06-10 — realtime-agent-tracing
- **Что:** Вкладка «Трейсинг» превращена из посмертной сводки в живую ленту наблюдаемости.
  - Хуки `PreToolUse`/`PostToolUse` расширены до matcher `.*` (`hooks/hooks.json`); фильтр значимых
    инструментов `TRACE_TOOLS` вынесен в Python (`scripts/telemetry_hook.py`).
  - Новые события `tool.start`/`tool.end` с `spanId="tool-"+toolUseId`, `tool`, `arg`, `ok`
    (`build_event`, `scripts/telemetry_hook.py:124`).
  - Оффсетное чтение хвоста `telemetry.jsonl` (`_iter_lines_from`) и лёгкая delta-лента `build_feed`
    (`scripts/_aipf.py:381`, `:432`); ленивый текст сообщений `parse_transcript_messages`
    (`scripts/_aipf.py:340`).
  - Новые эндпоинты `GET /trace/feed` (delta-only, курсор по байтам) и `GET /trace/messages`
    (ленивый, UTF-8) — `scripts/server.py:332`, `:352`. `/trace` и Langfuse-форвардинг не тронуты.
  - UI: живая лента по лейнам с автообновлением + свёрнутые раскрываемые сообщения агента
    (`templates/dashboard.html`).
  - Bootstrap базы знаний `docs/knowledge/` (этот документ и соседние).
- **Зачем:** показать, что агент делает прямо сейчас (поток инструментов + сообщения с таймингами), не
  ломая существующую сводку токенов/гант и не деградируя горячий путь хука. Рост `telemetry.jsonl` на
  порядок потребовал оффсетного чтения вместо полного.
- **План:** `.workflow/tasks/realtime-agent-tracing/plan.md`
- **ADR:** `decisions/ADR-0001-feed-delta-only-stateless.md`,
  `decisions/ADR-0002-matcher-wildcard-python-noise-filter.md`,
  `decisions/ADR-0003-lanes-best-effort-shared-session.md`
