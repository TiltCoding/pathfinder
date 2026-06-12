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
- [areas/telemetry-tracing.md](areas/telemetry-tracing.md) — телеметрия и вкладка «Трейсинг»: схема событий, контракты `/trace/feed` и `/trace/messages`, ловушки. _темы: телеметрия, трейсинг, хуки, лента_
- [areas/dashboard-changes-tab.md](areas/dashboard-changes-tab.md) — вкладка «Изменения»: `/changes` и `/changes?file=`, поле `untracked`, дерево файлов на фронте, встроенная подсветка diff. _темы: дашборд, git, дерево, подсветка_
- [areas/orchestrator-skills.md](areas/orchestrator-skills.md) — анатомия скиллов-оркестраторов (`/feature`, `/new-product`): регистрация конвенцией каталогов, паттерн SKILL.md + reference-файлы, frontmatter агентов с `model:`, ростеры `wf-*` vs `np-*`, правило «модель глобальна для subagent_type» и «субагенты не спавнят субагентов», карта стадий + build-loop. _темы: оркестратор, скиллы, под-агенты, регистрация, модели_

## Решения (decisions/)
- [decisions/ADR-0001-feed-delta-only-stateless.md](decisions/ADR-0001-feed-delta-only-stateless.md) — лента delta-only/stateless, склейка start↔end на клиенте, курсор по байтам
- [decisions/ADR-0002-matcher-wildcard-python-noise-filter.md](decisions/ADR-0002-matcher-wildcard-python-noise-filter.md) — matcher хуков `.*` + фильтр шума `TRACE_TOOLS` в Python
- [decisions/ADR-0003-lanes-best-effort-shared-session.md](decisions/ADR-0003-lanes-best-effort-shared-session.md) — лейны best-effort: общий session_id не даёт различить под-агента
- [decisions/ADR-0004-inline-syntax-highlight-no-cdn.md](decisions/ADR-0004-inline-syntax-highlight-no-cdn.md) — подсветка diff встроенным токенайзером без CDN (построчно, поверх `esc()`)
- [decisions/ADR-0005-untracked-noise-filter-zero-byte-toggle.md](decisions/ADR-0005-untracked-noise-filter-zero-byte-toggle.md) — фильтр untracked-мусора по 0 байт + тумблер «только tracked / все»
- [decisions/ADR-0006-np-agent-roster-model-pinning.md](decisions/ADR-0006-np-agent-roster-model-pinning.md) — отдельный ростер `np-*` с моделью в `model:`-frontmatter (fable-мыслитель / opus-исполнители); почему не реюз `wf-*` (модель глобальна для subagent_type); урезанный tool-set мыслителя как структурная «digest-only» гарантия
- [decisions/ADR-0007-evolutionary-build-loop.md](decisions/ADR-0007-evolutionary-build-loop.md) — эволюционный build-loop `/new-product`: гибридный гейт (тесты — стена, судья — руль), вердикт-объект, изолированный судья на измерение, замороженные PRD-тесты (анти-гейминг), Reflexion-scratchpad, три стоп-условия + анти-осцилляция, контекстный `approve-plan`, гейт-политика V1

_updated: 2026-06-12_
