# База знаний проекта — указатель

> Это карта знаний **для агентов**. Читайте её первой перед исследованием/планированием/кодингом.
> Одна строка на документ: ссылка + хук + темы. Держите указатель актуальным.

## Документы
- [architecture.md](architecture.md) — конвейер наблюдаемости: хуки → telemetry.jsonl → server → дашборд + Langfuse. _темы: структура, потоки данных, точки входа_
- [conventions.md](conventions.md) — паттерны кода: stdlib-only, exit-0, UTF-8, оффсетное чтение. _темы: стиль, ошибки, производительность_
- [glossary.md](glossary.md) — термины трейсинга: спан, лейн, tool.start/end, spanId, курсор-оффсет. _темы: домен, модель_
- [integrations.md](integrations.md) — Claude Code, Langfuse, env-ключи, файлы-артефакты. _темы: интеграции, env_
- [task-log.md](task-log.md) — журнал задач: что и зачем менялось. _темы: история, решения_

## Области (areas/)
- [areas/telemetry-tracing.md](areas/telemetry-tracing.md) — телеметрия и вкладка «Трейсинг»: схема событий (вкл. `tool.start.kind`/MCP), контракты `/trace/feed`, `/trace/messages`, `/trace/actions`, sidecar `meta.json` и мост спан→транскрипт, ловушки. _темы: телеметрия, трейсинг, хуки, лента, MCP, атрибуция_
- [areas/dashboard-changes-tab.md](areas/dashboard-changes-tab.md) — вкладка «Изменения»: `/changes` и `/changes?file=`, поле `untracked`, дерево файлов на фронте, встроенная подсветка diff. _темы: дашборд, git, дерево, подсветка_
- [areas/dashboard-feedback-ui.md](areas/dashboard-feedback-ui.md) — ввод обратной связи на вкладке «Контент»: свой ответ на choice (`answer` вне `options`, перебивает опцию), коммент к демо-варианту (`comment` с `blockId=vr.id`, footer всегда), модель draft, якоря, дедуп, рендер реплаев. _темы: дашборд, обратная связь, draft, answer, comment_
- [areas/orchestrator-skills.md](areas/orchestrator-skills.md) — анатомия скиллов-оркестраторов (`/feature`, `/new-product`): регистрация конвенцией каталогов, паттерн SKILL.md + reference-файлы, frontmatter агентов с `model:`, ростеры `wf-*` vs `np-*`, правило «модель глобальна для subagent_type» и «субагенты не спавнят субагентов», карта стадий + build-loop. _темы: оркестратор, скиллы, под-агенты, регистрация, модели_
- [areas/parallel-runs-hub.md](areas/parallel-runs-hub.md) — параллельные запуски в git worktree + хаб всех задач: общий store через симлинк `<worktree>/.workflow → <main>/.workflow`, `worktreePath`/`branch` в state и per-worktree diff (`_task_root`/`_git cwd`), контракты `GET /hub.json` (карточки `runs[]` + `analytics`) и `GET /hub` (3 секции), критерий active/history (q7), событийная аналитика без токенов, per-session `active.json`, CLI `scripts/worktree.py`. _темы: worktree, хаб, общий store, аналитика, история, атрибуция сессий_

## Решения (decisions/)
- [decisions/ADR-0001-feed-delta-only-stateless.md](decisions/ADR-0001-feed-delta-only-stateless.md) — лента delta-only/stateless, склейка start↔end на клиенте, курсор по байтам
- [decisions/ADR-0002-matcher-wildcard-python-noise-filter.md](decisions/ADR-0002-matcher-wildcard-python-noise-filter.md) — matcher хуков `.*` + фильтр шума `TRACE_TOOLS` в Python
- [decisions/ADR-0003-lanes-best-effort-shared-session.md](decisions/ADR-0003-lanes-best-effort-shared-session.md) — лейны best-effort: общий session_id не даёт различить под-агента
- [decisions/ADR-0004-inline-syntax-highlight-no-cdn.md](decisions/ADR-0004-inline-syntax-highlight-no-cdn.md) — подсветка diff встроенным токенайзером без CDN (построчно, поверх `esc()`)
- [decisions/ADR-0005-untracked-noise-filter-zero-byte-toggle.md](decisions/ADR-0005-untracked-noise-filter-zero-byte-toggle.md) — фильтр untracked-мусора по 0 байт + тумблер «только tracked / все»
- [decisions/ADR-0006-np-agent-roster-model-pinning.md](decisions/ADR-0006-np-agent-roster-model-pinning.md) — отдельный ростер `np-*` с моделью в `model:`-frontmatter (fable-мыслитель / opus-исполнители); почему не реюз `wf-*` (модель глобальна для subagent_type); урезанный tool-set мыслителя как структурная «digest-only» гарантия
- [decisions/ADR-0007-evolutionary-build-loop.md](decisions/ADR-0007-evolutionary-build-loop.md) — эволюционный build-loop `/new-product`: гибридный гейт (тесты — стена, судья — руль), вердикт-объект, изолированный судья на измерение, замороженные PRD-тесты (анти-гейминг), Reflexion-scratchpad, три стоп-условия + анти-осцилляция, контекстный `approve-plan`, гейт-политика V1
- [decisions/ADR-0008-feedback-on-existing-contract-zero-server.md](decisions/ADR-0008-feedback-on-existing-contract-zero-server.md) — свой ответ как `answer` вне `options` (перебивает опцию, один answer/вопрос) и коммент к варианту как `comment` с `blockId=vr.id` (footer всегда); 0 правок сервера — бэкенд агностичен к содержимому items
- [decisions/ADR-0009-transcript-attribution-and-actions-endpoint.md](decisions/ADR-0009-transcript-attribution-and-actions-endpoint.md) — поправка к ADR-0003: достоверная по-агентная атрибуция через транскрипт + `meta.json.toolUseId` (мост спан→транскрипт), ленивый `/trace/actions` вместо утяжеления `build_trace`, MCP в ленте дозаписью `kind/server/mcpTool`; lane остаётся best-effort, у задачи бывает несколько session_id
- [decisions/ADR-0010-shared-store-symlink-worktree.md](decisions/ADR-0010-shared-store-symlink-worktree.md) — общий store через симлинк `<worktree>/.workflow → <main>/.workflow` (vs env `AIPF_WORKFLOW_ROOT` vs реестр `runs.json`), `worktreePath`/`branch` в state для per-worktree diff, per-session `active.json` обязателен, токены вне кросс-задачного агрегата, хаб — отдельная страница `/hub` (не вкладка)

_updated: 2026-06-13_
