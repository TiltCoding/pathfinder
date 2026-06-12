# Область: Телеметрия и вкладка «Трейсинг»

> Подсистема наблюдаемости: сбор событий хуками, их хранение в `telemetry.jsonl`, отдача живой ленты и
> сообщений агента в дашборд, плюс асинхронный форвард в Langfuse.

## Назначение

Показать во вкладке «Трейсинг», **что агент делает прямо сейчас**: поток вызовов инструментов
(Bash/Read/Grep/Glob/Edit/Write) с таймингами по лейнам, плюс свёрнутые раскрываемые сообщения агента.
Раньше «Трейсинг» был посмертной сводкой токенов/стоимости только по под-агентам; теперь это дополнено
живой лентой действий (существующая сводка/гант сохранены).

## Ключевые файлы

- `hooks/hooks.json` — подписка на события CC. `PreToolUse`/`PostToolUse` matcher = `.*` (ловят **все**
  инструменты); фильтр шума вынесен в Python. `SessionStart/SessionEnd/Stop/SubagentStop` — без matcher.
- `scripts/telemetry_hook.py:70` — `build_event(payload)`: единственная точка, превращающая JSON хука в
  одну строку `telemetry.jsonl`. Ветки: `session.start/end`, `turn.stop`, `subagent.start/end`,
  `file.touch`, `tool.start/tool.end`.
- `scripts/telemetry_hook.py:37` — `TRACE_TOOLS` (фильтр значимых инструментов для ленты) и
  `_TRACE_ARG_FIELD` (`scripts/telemetry_hook.py:40`) — карта «инструмент → ключевой аргумент».
- `scripts/_aipf.py:76` — `append_jsonl`: атомарная дозапись одной JSON-строки (горячий путь хука).
- `scripts/_aipf.py:381` — `_iter_lines_from(path, offset)`: оффсетное чтение хвоста файла (курсор).
- `scripts/_aipf.py:432` — `build_feed`: лёгкая delta-лента действий (никогда не читает транскрипты).
- `scripts/_aipf.py:340` — `parse_transcript_messages`: единственная функция, читающая прозу транскрипта.
- `scripts/_aipf.py:515` — `build_trace`: тяжёлая посмертная модель `/trace` (спаны + usage).
- `scripts/server.py:185` — роутинг `/trace/feed`; `scripts/server.py:332` — `_trace_feed`.
- `scripts/server.py:193` — роутинг `/trace/messages`; `scripts/server.py:352` — `_trace_messages`,
  `scripts/server.py:383` — `_resolve_transcript`.
- `templates/dashboard.html` — вкладка «Трейсинг»: `traceTick`/`renderTrace`, инкрементальный опрос ленты
  по оффсету, делегированный обработчик раскрытия сообщений.

## Схема событий `telemetry.jsonl`

Одна строка = одно событие. Базовые поля (все события): `ts` (ISO-8601 UTC `Z`), `session_id`, `event`.

| `event`          | дополнительные поля                                                                    |
|------------------|----------------------------------------------------------------------------------------|
| `session.start`  | `phase`, `iteration`, `summary` (source)                                               |
| `session.end`    | `summary` (reason)                                                                      |
| `turn.stop`      | `phase`, `iteration`                                                                    |
| `subagent.start` | `role`, `spanId="span-"+toolUseId`, `toolUseId`, `bg`, `summary`(description), `phase`, `iteration` |
| `subagent.end`   | `role`, `spanId`, `toolUseId`, `ok`, `summary`(tool_result, обрезка 500)               |
| `file.touch`     | `tool`, `file` (file_path), `phase`, `iteration`                                       |
| `tool.start`     | `tool`, `toolUseId`, `spanId="tool-"+toolUseId`, `arg` (обрезка 200), `phase`, `iteration` |
| `tool.end`       | `tool`, `toolUseId`, `spanId`, `ok` (по `tool_result.is_error`/`status`)               |

Ключевые детали `tool.*` (новое в задаче realtime-agent-tracing):
- **Связка start↔end** — по `spanId = "tool-" + tool_use_id`. У `Pre` и `Post` один `tool_use_id`
  (`toolu_...`), это надёжный join. Тот же id совпадает с `content[].id` блока `tool_use` в транскрипте.
- **`arg`** — один ключевой аргумент по карте `_TRACE_ARG_FIELD`: `Bash→command`, `Read/Edit/Write/
  MultiEdit/NotebookEdit→file_path`, `Grep/Glob→pattern`. Только в `tool.start` (в `end` join по `spanId`).
- **`tool.*` ≠ `file.touch`.** `file.touch` сохранён как есть — его маппит Langfuse event-create; `tool.*`
  идёт надмножеством рядом и в Langfuse не форвардится.
- **Под-агенты исключены** из `tool.*` (`TRACE_TOOLS` не содержит `Task`/`Agent`) — у них богаче
  `subagent.*`, дублировать не нужно.

## Публичный интерфейс

### `GET /trace/feed?slug=<slug>&since=<byteOffset>` — delta-лента действий

- **Stateless, delta-only.** Читает только хвост `telemetry.jsonl` за `since` байт. `since=0` → весь файл.
- Ответ: `{events: [...], nextOffset, generatedAt}`. Клиент на следующем тике передаёт `since=nextOffset` —
  читается только дозаписанное.
- Каждый `event` — **плоская** запись: `{spanId, tool, event:"start"|"end", ts, role, lane, session_id}`,
  для `start` доп. `arg`, для `end` доп. `ok`. **Сервер НЕ склеивает start/end в спаны** — это делает
  клиент инкрементально по `spanId`, он же выводит `running` (есть `start`, нет `end`).
- `lane` — дорожка группировки: `"orchestrator"` либо `spanId` под-агента (best-effort, см. подводные камни).

### `GET /trace/messages?slug=<slug>&agent=<spanId|role>&session=<id>` — сообщения агента (ленивый)

- Вызывается **только** по явному раскрытию конкретного агента в UI (бриф: «не тащить лишнюю прозу»).
- Ответ: `{messages: [{ts, relMs, text}], pending}`. `relMs` — мс от первого сообщения агента.
- `pending:true` — транскрипт ещё не существует (graceful degrade), UI показывает «подгружаются».
- Текст читается строго в UTF-8 (`parse_transcript_messages`, `scripts/_aipf.py:340`).

### `GET /trace?slug=<slug>` — посмертная модель (без изменений)

Спаны под-агентов + usage из транскриптов, агрегаты и гант. mtime-кэш 3 с.

## Расположение транскриптов

- Главная сессия: `~/.claude/projects/<proj>/<sessionId>.jsonl`
- Под-агенты: `~/.claude/projects/<proj>/<sessionId>/subagents/agent-<agentId>.jsonl`
- `<proj>` — путь проекта с заменой разделителей на дефис
  (`c--Projects-personal-ai-pathfinder`). Локация — `find_main_transcript`/`find_subagent_files`
  (`scripts/_aipf.py:253`).
- Формат — JSONL, запись на строку: `type` (`user`/`assistant`), `timestamp`, `message.content[]` (блоки
  `text`/`tool_use`/`tool_result`), `message.usage`, `attributionAgent` (роль под-агента, только у его
  assistant-записей).

## Инварианты

- Хук пишет **ровно одну** строку на событие append-ом; любая ошибка → `exit 0`, воркфлоу не ломается.
- `/trace/feed` и `/trace/messages` — **read-only**; курсор Langfuse (`telemetry.cursor`) ими не затрагивается.
- Курсор ленты — **байтовый оффсет** (`f.tell()`), не номер строки; переживает докатку строк в файл.
- `spanId` стабилен между `start` и `end` (`"tool-"+toolUseId` / `"span-"+toolUseId`).
- Новые типы событий добавляются **только** дозаписью; формат/порядок старых событий не меняется (иначе
  собьётся обогащение Langfuse `telemetry.enriched.json`).
- Парность Pre/Post **не гарантирована**: при прерывании инструмента `tool.start` останется без `end` —
  это валидное состояние `running`, UI/сервер обязаны его терпеть.

## Подводные камни

- **Общий `session_id` у под-агентов (важно, нетривиально).** Из payload хука НЕЛЬЗЯ различить, какой
  под-агент выполнил `tool.*`: оркестратор и все под-агенты делят один `session_id`. Поэтому `lane`
  определяется **best-effort** (`_feed_lane`, `scripts/_aipf.py:413`): если в сессии открыт ровно один
  под-агентский спан — действие приписывается ему; иначе — `"orchestrator"`. При нескольких параллельных
  под-агентах атрибуция неточна. Это ограничение источника, не баг (exploration §5).
- **Кириллица в консоли ≠ повреждение файла.** Транскрипты и `telemetry.jsonl` корректны в UTF-8;
  «кракозябры» в консоли — это cp1251 stdout на Windows. Всегда читать файлы с `encoding="utf-8"`
  (`_iter_lines`, `parse_transcript_messages`), не полагаться на консольный рендер.
- **Матчер `.*` запускает хук на КАЖДЫЙ инструмент**, включая нетрейсимые (TodoWrite и т.п.) — они
  падают сквозь все ветки `build_event` и выходят без записи. Цена — лишние запуски `python3`; принятый
  компромисс (см. ADR-0002).
- **`tool_input` может быть не-dict или отсутствовать** — извлечение `arg` обязательно проверяет
  `isinstance(tool_input, dict)` (`_trace_arg`, `scripts/telemetry_hook.py:161`).
- **Атрибуция lane при `since>0`.** Если `subagent.start` под-агента остался до курсора, его tool-действия
  в дельте деградируют до `"orchestrator"`; клиент корректирует по своей накопленной модели (`build_feed`
  docstring, `scripts/_aipf.py:449`).
- **`/trace` и `build_trace` читают файл целиком** — это узкое место при росте `telemetry.jsonl`; именно
  поэтому живая лента вынесена в отдельный оффсетный `build_feed`, а не подмешана в `/trace`.

## Как расширять

- **Новый тип события телеметрии:** добавить ветку в `build_event` (`scripts/telemetry_hook.py:70`),
  только дозаписью новых полей; не менять старые. Если событие должно уходить в Langfuse — добавить маппинг
  в `events_to_langfuse_batch` (`scripts/_aipf.py`), иначе оно пропускается форвардером автоматически.
- **Новый инструмент в ленте:** добавить имя в `TRACE_TOOLS` и (опц.) поле в `_TRACE_ARG_FIELD`.
- **Новое поле в delta-ленте:** дополнить запись в `build_feed` (`scripts/_aipf.py:478`) и потребление в
  `renderTrace` дашборда; помнить про дифф-рендер по `spanId` (не сбрасывать скролл/раскрытие).
- **Новый трейс-эндпоинт:** ветка в `do_GET` (`scripts/server.py:160`) + метод (по образцу `_trace_feed`);
  для живых данных — короткий кэш (≤1 с) или без кэша, slug валидировать `safe_slug`.

_updated: 2026-06-10_
