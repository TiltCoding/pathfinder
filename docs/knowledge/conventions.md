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
- **Всё человекочитаемое — на русском** (UI, тексты артефактов, эта база знаний).

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

## Полезные утилиты (переиспользовать)

- `scripts/_aipf.py:76` — `append_jsonl(path, obj)` — атомарная дозапись одной JSON-строки.
- `scripts/_aipf.py:381` — `_iter_lines_from(path, offset)` — оффсетное чтение хвоста (курсор).
- `scripts/_aipf.py:372` — `_iter_lines(path)` — построчное чтение всего файла (UTF-8, терпит ошибки).
- `scripts/_aipf.py:93` — `active_slug(root, session_id)` — резолв активной задачи.
- `scripts/_aipf.py:85` — `slug_from_workspace_path(text)` — slug из пути `.workflow/tasks/<slug>/`.
- `scripts/_aipf.py:25` — `now_iso_utc()` — таймстемп ISO-8601 UTC `Z`.
- `scripts/_aipf.py:496` — `_spans_from_events(events)` — склейка start/end в спаны (паттерн парности).

_updated: 2026-06-16_
