# Архитектура

> Обзор с высоты 10 000 футов: что прочитать прежде, чем трогать наблюдаемость плагина.
> Узкий фокус документа — конвейер телеметрии и трейсинга (то, вокруг чего идёт основная работа).

## Модули и ответственности

- **Хуки Claude Code** (`hooks/hooks.json`) — подписка на события CC (Session*, Stop, Pre/PostToolUse,
  SubagentStop). Каждое событие зовёт один диспетчер.
- **Диспетчер телеметрии** (`scripts/telemetry_hook.py`) — превращает JSON хука со stdin в **одну** строку
  `telemetry.jsonl`. Горячий путь: только append в локальный файл, без сети.
- **Общие хелперы** (`scripts/_aipf.py`) — резолв задачи/контекста, атомарная дозапись, оффсетное чтение,
  парсинг транскриптов, сборка моделей `/trace` (тяжёлой) и `/trace/feed` (лёгкой).
- **Сервер дашборда** (`scripts/server.py`) — stdlib-only HTTP: отдаёт модель трейса и живую ленту, а в
  фоне форвардит телеметрию в Langfuse.
- **Дашборд** (`templates/dashboard.html`) — статичный data-driven HTML без CDN; вкладка «Трейсинг»
  опрашивает ленту и лениво подгружает сообщения агента.

## Границы и поток данных

Конвейер наблюдаемости (слева направо):

```
хуки CC ──stdin JSON──▶ telemetry_hook.py ──append 1 строка──▶ .workflow/tasks/<slug>/telemetry.jsonl
                                                                         │
                       ┌─────────────────────────────────────────────────┼──────────────────────────────┐
                       ▼                                ▼                  ▼                               ▼
                 server.py /trace            server.py /trace/feed   server.py /trace/messages   TelemetryForwarder
              (тяжёлая модель: спаны          (лёгкая delta-лента,    (ленивый текст из           (асинхронный форвард
               + usage из транскриптов)        курсор по байтам)       транскриптов, UTF-8)        в Langfuse, курсор)
                       │                                │                  │
                       └────────────────────────────────┴──────────────────┘
                                              ▼
                                       dashboard.html (вкладка «Трейсинг»)
```

Два независимых пути потребления одного файла `telemetry.jsonl`:

1. **Дашборд (read-only).** Три эндпоинта читают файл и ничего не пишут:
   - `GET /trace` — посмертная модель: спаны под-агентов (`subagent.*`) джойнятся с числами usage из
     транскриптов; mtime-кэш 3 с. Сборка — `_aipf.build_trace` (`scripts/_aipf.py:515`).
   - `GET /trace/feed` — живая лента действий: только `tool.*`-события, delta-only по байтовому курсору.
     Сборка — `_aipf.build_feed` (`scripts/_aipf.py:432`); кэш ≤1 с (`scripts/server.py:332`).
   - `GET /trace/messages` — текст сообщений агента, лениво, по явному запросу (`scripts/server.py:352`).
2. **Langfuse-форвардинг (write-курсор).** `TelemetryForwarder` читает `telemetry.jsonl`, по курсору
   `telemetry.cursor` (число отправленных строк) шлёт **только новые** события батчем; курсор двигается
   только после 2xx (at-least-once). Неизвестные типы событий (`turn.stop`, новые `tool.*`) намеренно НЕ
   форвардятся — см. `scripts/_aipf.py` (`events_to_langfuse_batch`). Поэтому добавление новых типов в поток
   безопасно. Подробнее — [integrations.md](integrations.md).

## Точки входа

- HTTP-сервер дашборда — `scripts/server.py` (роутинг `do_GET` со `scripts/server.py:160`).
- Диспетчер телеметрии (CLI/хук) — `scripts/telemetry_hook.py` (`main` с `scripts/telemetry_hook.py:182`).

## Сквозные механизмы

- **Контекст задачи.** Каждое событие привязывается к задаче (`slug`) и фазе/итерации из `state.json`
  через `resolve_slug`/`active_slug` (`scripts/telemetry_hook.py:52`, `scripts/_aipf.py:93`).
- **Обработка ошибок.** Хук — «никогда не ломать воркфлоу»: любая ошибка → `exit 0`. Сервер — «никогда не
  ронять страницу»: исключения в эндпоинтах ловятся и отдают пустую/ошибочную модель. См.
  [conventions.md](conventions.md).
- **Транскрипты.** Источник текста сообщений и чисел usage — JSONL-файлы CC в
  `~/.claude/projects/<proj>/...`. Читаются только UTF-8. Локация и формат — в области
  [areas/telemetry-tracing.md](areas/telemetry-tracing.md).

_updated: 2026-06-10_
