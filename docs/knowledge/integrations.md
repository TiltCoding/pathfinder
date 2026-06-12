# Интеграции и конфигурация

> Внешние сервисы, API и ключи конфигурации. **Только имена, никогда значения секретов.**

## Внешние сервисы

- **Claude Code (хост-агент)** — источник событий и транскриптов. Хуки регистрируются в `hooks/hooks.json`
  и зовут `scripts/telemetry_hook.py`. Транскрипты сессий/под-агентов CC лежат в
  `~/.claude/projects/<proj>/...` и читаются (только UTF-8) для usage и текста сообщений
  (`scripts/_aipf.py:253`).
- **Langfuse** — внешняя система трейсинга. `TelemetryForwarder` (`scripts/server.py:874`) асинхронно
  форвардит события `telemetry.jsonl` батчем на `<LANGFUSE_HOST>/api/public/ingestion`
  (`scripts/_aipf.py:210`).
  - Форвард **курсорный**: `telemetry.cursor` хранит число отправленных строк; курсор двигается только
    после 2xx (at-least-once). Неизвестные типы событий (`turn.stop`, `tool.*`) намеренно не маппятся и
    пропускаются — добавление новых типов безопасно.
  - Обогащение usage под-агентов: `telemetry.enriched.json` (set уже отправленных `spanId`),
    `scripts/server.py:936`.
  - Если `LANGFUSE_*` не заданы — режим «local only», форвард отключён (`scripts/server.py:1030`).

## Переменные окружения / конфиг

- `LANGFUSE_PUBLIC_KEY` — публичный ключ Langfuse (`scripts/_aipf.py:128`). Значение — в секретах.
- `LANGFUSE_SECRET_KEY` — секретный ключ Langfuse (`scripts/_aipf.py:129`). Значение — в секретах.
- `LANGFUSE_HOST` — базовый URL Langfuse; по умолчанию `https://cloud.langfuse.com` (`scripts/_aipf.py:130`).
- `CLAUDE_PLUGIN_ROOT` — корень установленного плагина; подставляется в команды хуков в `hooks/hooks.json`.

## Файлы-артефакты задачи (`.workflow/tasks/<slug>/`)

- `telemetry.jsonl` — журнал событий (append-only).
- `telemetry.cursor` — курсор Langfuse (число отправленных строк).
- `telemetry.enriched.json` — set `spanId`, для которых usage уже отправлен в Langfuse.
- `state.json` / `active.json` — фаза/итерация и активная задача (резолв контекста хуком).

_updated: 2026-06-10_
