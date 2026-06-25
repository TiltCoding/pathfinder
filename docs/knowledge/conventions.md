# Соглашения и паттерны

> Замеченные в коде конвенции — чтобы держать единый стиль и переиспользовать существующее.

## Нейминг и структура

- Скрипты сервера/хуков — в `scripts/`; общие хелперы — в `scripts/_aipf.py` (импортируется и хуком, и
  сервером). Шаблоны UI — в `templates/`. Хуки — в `hooks/hooks.json`.
- Артефакты задачи лежат в `.workflow/tasks/<slug>/` (`telemetry.jsonl`, `state.json`, `active.json`,
  `telemetry.cursor`, `telemetry.enriched.json`). Имена строятся через `task_file` (`scripts/_aipf.py:52`).
- События телеметрии именуются `<сущность>.<фаза>`: `session.start`, `subagent.end`, `tool.start`.
- `spanId` строится по шаблону `<префикс>-<toolUseId>` (`tool-`, `span-`).

## Обработка ошибок и логирование

- **Хук никогда не ломает воркфлоу.** Любая ошибка в `telemetry_hook.py` → `return 0` молча
  (`scripts/telemetry_hook.py:182`). Вся логика — внутри `try` в `main`; вне сети, без тяжёлой работы.
- **Сервер никогда не роняет страницу.** Каждый эндпоинт ловит исключения и отдаёт пустую/ошибочную модель
  вместо 500 (напр. `_trace` `scripts/server.py:319`, `_trace_feed` `scripts/server.py:343`,
  `_trace_messages` `scripts/server.py:366`).
- **Только добавление в формат телеметрии.** Новые типы событий/поля — дозаписью; формат и порядок старых
  событий не менять (иначе собьётся курсор/обогащение Langfuse).
- **Парность start/end не предполагать.** Код терпит спаны без `end` (прерванный инструмент = `running`).

## Технологические ограничения

- **Сервер — только stdlib Python.** Никаких внешних зависимостей в `scripts/server.py`.
- **Дашборд без CDN.** `templates/dashboard.html` — статичный data-driven HTML; markdown/рендер — встроены
  (`md`), без внешних загрузок.
- **Файлы читать в UTF-8.** Всегда `encoding="utf-8"` (`_iter_lines`, `parse_transcript_messages`); не
  полагаться на консольный рендер кириллицы (cp1251 stdout на Windows искажает вывод, но не файлы).
- **Язык вывода = язык запроса человека (побеждает)** — авто-детект из его сообщения, резолвится на
  INTAKE в `state.json.lang` и передаётся под-агентам. Глобальная настройка
  (`~/.claude/ai-pathfinder/settings.json`, `en` при отсутствии — **eng-first**) — лишь **фолбэк**, когда
  человеческого запроса нет (autonomous/eval), и язык хрома UI дашборда. Эволюция правила: жёсткое «всё
  человекочитаемое → русский» → eng-first + чат-исключение (ADR-0018) → **язык запроса побеждает везде**
  (ADR-0022). Классификация:
  - **Язык запроса** (всё, что читает человек): нарратив оркестратора в терминале, дашборд, brief/
    exploration/plan/questions/summary/PRD, тексты гейтов и опции choice, `replies.json`/`chat.jsonl`.
  - **Всегда английский** (если человек явно не попросил иначе): `docs/knowledge/**`, README, git-коммиты.
  - **Стабильно английские** машинно-парсимые заголовки/ключи (секции дайджестов, `cand:`-ключи, схемы) —
    не переводятся никогда.
  UI дашборда/хаба локализован (`en`/`ru`, `areas/dashboard-i18n.md`); эта база знаний — на русском
  исторически (consistency с существующими доками), новые доки по правилу — английские.

## Производительность

- **Горячий путь хука дёшев** — только `append_jsonl` одной строки (`scripts/_aipf.py:76`), извлечение
  `arg` с обрезкой и проверкой типа (`_trace_arg`, `scripts/telemetry_hook.py:161`).
- **Живые эндпоинты — оффсетное чтение.** Лента читает только хвост файла (`_iter_lines_from`,
  `scripts/_aipf.py:381`); полное чтение (`build_trace`) — только в тяжёлом `/trace` с кэшем 3 с.

## Тесты

- **Оффлайн stdlib-`unittest`, без внешних зависимостей и сети.** Все тесты в `tests/*.py` —
  чистый `unittest`; ни git, ни HTTP, ни диск вне tempfile. Цель — покрывать «тихие критические
  пути» (маппинг событий, чтение хвоста, парсеры) детерминированно.
- **`sys.path`-хак для импорта `scripts/`** в шапке каждого файла (одинаковый блок):
  `_SCRIPTS = .../scripts; sys.path.insert(0, _SCRIPTS)` → `import _aipf` и т. п. Файлы работают и как
  `python3 tests/test_x.py`, и как `python3 -m unittest tests.test_x`.
- **Изоляция через tempfile + cleanup.** Где нужен диск — `tempfile.mkdtemp()` + `addCleanup`
  (или `shutil.rmtree` в `tearDown`); фикстуры `telemetry.jsonl` строятся под `_aipf.task_file(...)`
  по форме `telemetry_hook.build_event`. Git/`_git` — через monkeypatch (фейковый `_git` отдаёт
  porcelain-вывод), git не вызывается.
- **Запуск всего набора:** `python3 -m unittest discover -s tests`.
- ⚠ **macOS-нюанс фикстур worktree-тестов:** tempdir пропускают через `os.path.realpath`, т.к.
  `tempfile.mkdtemp` отдаёт путь под `/var` (симлинк на `/private/var`), а `_aipf.workflow_base`
  (`scripts/_aipf.py:41`) симлинки не резолвит — без нормализации идемпотентная ветка
  `_ensure_workflow_symlink` (`scripts/worktree.py:202`) уходит в warning. Это особенность теста;
  потенциальная хрупкость прод-сравнения — см. task-log `tests-silent-critical-paths`.

### Кросс-платформенность тестов (Linux/macOS/Windows)

Набор обязан проходить на всех трёх ОС матрицы CI без правок прод-кода — чинится **непортируемость
теста**, а не «баг продукта». Паттерны (задача `ci-cross-platform-tests`, 2026-06-17):

- **Skip по реальной способности, а не по `os.name`.** Symlink-тесты гейтятся пробой `os.symlink` во
  временной директории (хелпер `_symlink_supported()` + константа `_SYMLINKS` + `@skipUnless(_SYMLINKS, …)`,
  `tests/test_worktree.py`). Так Windows с Developer Mode / админ-CI, где симлинки **работают**, не
  глушится зря — пропуск только когда возможность реально отсутствует.
- **`@skipIf(os.name == "nt")` — только для платформенно-зависимой семантики ОС.** Pid-проба
  `os.kill(pid, 0)` на Windows кидает `OSError(WinError 87)`, а не `ProcessLookupError`, поэтому
  dead-pid тесты `tests/test_server_health.py` скипаются на `nt`. Продукт намеренно консервативен (любой
  не-`ProcessLookupError` = «жив», чтобы не выбросить рабочий сервер) — его НЕ трогаем, скипается тест.
- **Фикстуры jsonl — с `newline=""`.** Открывать файлы телеметрии под запись с `newline=""`
  (`tests/test_feed.py`): иначе на Windows `\n` транслируется в `\r\n`, а байтовый курсор `_iter_lines_from`
  (`scripts/_aipf.py:381`) считает оффсеты по байтам и сбивается на лишнем `\r`. Формат на диске — строго LF.
- **Пути в тестах строить от `tempfile`/`os.path.realpath`, сравнивать через `os.path.abspath`.** Никаких
  POSIX-литералов (`/repo/main`) в ассертах — на Windows реальный путь `C:\repo\main`. Базу берут из
  `os.path.realpath(tempfile.mkdtemp())`, ожидание считают тем же `os.path.*` API
  (`ListWorktreesPorcelainTest`, `tests/test_worktree.py`).

### CI

- **`.github/workflows/ci.yml`** — `on: [push, pull_request]`, два job'а:
  - **`test`** — matrix `os: [ubuntu/macos/windows-latest] × python: [3.11, 3.12, 3.13]`, `fail-fast: false`
    (видеть все падающие комбинации сразу), единственный шаг `python -m unittest discover -s tests`.
  - **`lint`** — single `ubuntu-latest` + python 3.13, шаг `python scripts/check_stdlib.py`: проверяет
    несущие инварианты — **stdlib-only** (`scripts/*.py` импортируют лишь stdlib + локальные модули) и
    **no-CDN** (`templates/*.html` без внешних `src`/`href`/`@import` на `http(s)`). Локально тот же гейт
    запускается через `dev.py lint`.

  Оба job'а **stdlib-only, без `pip install`** (соответствует инварианту «сервер/тесты — только stdlib»).
  Любой новый тест обязан укладываться в этот безпиповый прогон.

## Атомарная запись (инвариант: per-process temp + retrying replace)

- **Любая** атомарная запись файла поверх общего store идёт через `_aipf.atomic_write` /
  `_aipf.write_json` (`scripts/_aipf.py:95`/`:111`) — **не** катать свой `tmp + os.replace`. Параллельные
  `/feature` делят store через симлинк (ADR-0010), поэтому несколько процессов/потоков пишут один файл
  конкурентно. Безопасность даёт связка двух мер (обе обязательны):
  - **Per-process temp** `atomic_temp_name(path)` (`scripts/_aipf.py:65`) = `path.<pid>.<uuid8>.tmp` —
    каждый писатель в свой temp (нет гонки на общий `path.tmp`, `os.replace` остаётся атомарным в той же FS).
  - **Retrying replace** `atomic_replace(tmp, path)` (`scripts/_aipf.py:76`) — `os.replace` с ограниченным
    ретраем (50×2 мс) **только** на транзиентную Windows-`PermissionError(13)` (destination держит другой
    писатель), на исчерпании re-raise. На POSIX срабатывает с первой попытки.
- `server.Workspace.write_json` (`scripts/server.py:141`) переиспользует **те же** хелперы — два
  центральных писателя не дрейфуют. **ADR-0021.**
- ⚠ **Долг:** `server.write_lang` (`scripts/server.py:209`) и attachment-upload (`scripts/server.py:1372`)
  ещё на фиксированном/pid-only temp **без** ретрая — не образец, подтянуть отдельной задачей
  (task-log `atomic-write-pid-temp`).

## Полезные утилиты (переиспользовать)

- `scripts/_aipf.py:95` — `atomic_write(path, text)` / `:111` `write_json(path, data)` — атомарная
  публикация файла (per-process temp + retrying replace, кросс-платформенно; см. инвариант выше, ADR-0021).
- `scripts/_aipf.py:76` — `append_jsonl(path, obj)` — атомарная дозапись одной JSON-строки.
- `scripts/_aipf.py:381` — `_iter_lines_from(path, offset)` — оффсетное чтение хвоста (курсор).
- `scripts/_aipf.py:372` — `_iter_lines(path)` — построчное чтение всего файла (UTF-8, терпит ошибки).
- `scripts/_aipf.py:93` — `active_slug(root, session_id)` — резолв активной задачи.
- `scripts/_aipf.py:85` — `slug_from_workspace_path(text)` — slug из пути `.workflow/tasks/<slug>/`.
- `scripts/_aipf.py:25` — `now_iso_utc()` — таймстемп ISO-8601 UTC `Z`.
- `scripts/_aipf.py:496` — `_spans_from_events(events)` — склейка start/end в спаны (паттерн парности).

_updated: 2026-06-25 (improve-overall-2: CI job `lint` подключён (stdlib-only + no-CDN гейт на push/PR, оба job'а описаны) + инвариант атомарной записи per-process temp + retrying replace (`atomic_write`/`atomic_replace`/`atomic_temp_name`, ADR-0021). Ранее — конвенция языка eng-first, ADR-0018)_
