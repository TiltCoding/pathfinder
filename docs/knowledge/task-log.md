# Журнал задач

> Append-only. Каждая задача воркфлоу оставляет запись: что и **зачем** изменено. История для будущих агентов.

<!-- Новые записи — сверху. -->

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
