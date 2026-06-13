# Область: Телеметрия и вкладка «Трейсинг»

> Подсистема наблюдаемости: сбор событий хуками, их хранение в `telemetry.jsonl`, отдача живой ленты и
> сообщений агента в дашборд, плюс асинхронный форвард в Langfuse.

## Назначение

Показать во вкладке «Трейсинг», **что агент делает прямо сейчас**: поток вызовов инструментов
(Bash/Read/Grep/Glob/Edit/Write) с таймингами по лейнам, плюс свёрнутые раскрываемые сообщения агента.
Раньше «Трейсинг» был посмертной сводкой токенов/стоимости только по под-агентам; затем это дополнено
живой лентой действий (существующая сводка/гант сохранены). В `agent-trace-details` посмертная карточка
агента стала **раскрываемой**: показывает описание задачи (`summary`) и по раскрытию — две ленивые секции:
«Действия» (хронология из `/trace/actions` с цветной типизацией и легендой-счётчиком `counts`) и «Сообщения
агента» (из `/trace/messages`); живая лента типизирует MCP-строки как `server · tool` по полям `kind`/`server`/`mcpTool`.

## Ключевые файлы

- `hooks/hooks.json` — подписка на события CC. `PreToolUse`/`PostToolUse` matcher = `.*` (ловят **все**
  инструменты); фильтр шума вынесен в Python. `SessionStart/SessionEnd/Stop/SubagentStop` — без matcher.
- `scripts/telemetry_hook.py:70` — `build_event(payload)`: единственная точка, превращающая JSON хука в
  одну строку `telemetry.jsonl`. Ветки: `session.start/end`, `turn.stop`, `subagent.start/end`,
  `file.touch`, `tool.start/tool.end` (последняя ловит и `mcp__*`, см. `_parse_mcp_name` `:195`,
  `_mcp_arg` `:210`).
- `scripts/telemetry_hook.py:37` — `TRACE_TOOLS` (фильтр значимых инструментов для ленты) и
  `_TRACE_ARG_FIELD` (`scripts/telemetry_hook.py:40`) — карта «инструмент → ключевой аргумент».
- `scripts/_aipf.py:76` — `append_jsonl`: атомарная дозапись одной JSON-строки (горячий путь хука).
- `scripts/_aipf.py:550` — `_iter_lines_from(path, offset)`: оффсетное чтение хвоста файла (курсор).
- `scripts/_aipf.py:614` — `build_feed`: лёгкая delta-лента действий (никогда не читает транскрипты).
- `scripts/_aipf.py:362` — `parse_transcript_messages`: читалка прозы транскрипта (текстовые блоки).
- `scripts/_aipf.py:451` — `parse_transcript_actions`: читалка действий агента из транскрипта (`tool_use` +
  `tool_result`); классификатор `_action_type` (`:422`), аргумент `_action_arg` (`:432`).
- `scripts/_aipf.py:263` — `find_subagent_meta`: чтение sidecar `agent-*.meta.json` (мост спан→транскрипт).
- `scripts/_aipf.py:528` — `build_trace`: тяжёлая посмертная модель `/trace` (спаны + usage + `summary`).
- `scripts/server.py:185` — роутинг `/trace/feed`; `scripts/server.py:342` — `_trace_feed`.
- `scripts/server.py:193`/`:199` — роутинг `/trace/messages`/`/trace/actions`; `scripts/server.py:362` —
  `_trace_messages`, `:394` — `_trace_actions`, `:439` — `_agent_description`, `:466` — `_resolve_transcript`.
- `tests/test_telemetry_actions.py` — offline-тесты: MCP-ветка `build_event` (поля `kind/server/mcpTool`,
  серверы с `_`, defensive на не-dict input) и `parse_transcript_actions` (статусы ok/error/running, MCP).
- `templates/dashboard.html` — вкладка «Трейсинг»: `traceTick`/`renderTrace`, инкрементальный опрос ленты
  по оффсету, делегированный обработчик раскрытия сообщений.

## Схема событий `telemetry.jsonl`

Одна строка = одно событие. Базовые поля (все события): `ts` (ISO-8601 UTC `Z`), `session_id`, `event`.

| `event`          | дополнительные поля                                                                    |
|------------------|----------------------------------------------------------------------------------------|
| `session.start`  | `phase`, `iteration`, `summary` (source)                                               |
| `session.end`    | `summary` (reason)                                                                      |
| `turn.stop`      | `phase`, `iteration`                                                                    |
| `phase`          | `phase`, `iteration`, `summary`; `session_id` обычно `null` (см. ниже)                  |
| `subagent.start` | `role`, `spanId="span-"+toolUseId`, `toolUseId`, `bg`, `summary`(description), `phase`, `iteration` |
| `subagent.end`   | `role`, `spanId`, `toolUseId`, `ok`, `summary`(tool_result, обрезка 500)               |
| `file.touch`     | `tool`, `file` (file_path), `phase`, `iteration`                                       |
| `tool.start`     | `tool`, `toolUseId`, `spanId="tool-"+toolUseId`, `arg` (обрезка 200), `kind`; для MCP ещё `server`, `mcpTool` |
| `tool.end`       | `tool`, `toolUseId`, `spanId`, `ok` (по `tool_result.is_error`/`status`)               |

**Событие `phase` (и `gate`) — пишет оркестратор, не хук.** В отличие от остального потока, эти строки
дозаписывает **скилл-оркестратор** прямо в `telemetry.jsonl` при смене стадии (`build_event` его не
порождает — `telemetry_hook.py` знает только `session.*`/`turn.stop`/`subagent.*`/`file.touch`/`tool.*`).
Реальная форма: `{"ts":…, "event":"phase", "session_id":null, "phase":"DONE", "iteration":1, "summary":null}`
(`session_id` обычно `null`, т.к. событие вне конкретной сессии). Форвардится в Langfuse **веткой
`phase`/`gate`** в `events_to_langfuse_batch` (`scripts/_aipf.py:212`) как `event-create` с
`name = "<kind>:<summary>"` и метаданными-тегами `phase`/`iteration`; `turn.stop` и неизвестные события
форвардер намеренно пропускает.

Ключевые детали `tool.*` (база — realtime-agent-tracing; MCP/`kind` — agent-trace-details):
- **Связка start↔end** — по `spanId = "tool-" + tool_use_id`. У `Pre` и `Post` один `tool_use_id`
  (`toolu_...`), это надёжный join. Тот же id совпадает с `content[].id` блока `tool_use` в транскрипте.
- **`arg`** — один ключевой аргумент по карте `_TRACE_ARG_FIELD`: `Bash→command`, `Read/Edit/Write/
  MultiEdit/NotebookEdit→file_path`, `Grep/Glob→pattern`. Только в `tool.start` (в `end` join по `spanId`).
- **MCP в ленте (`agent-trace-details`).** В ту же `tool.*`-ветку (`scripts/telemetry_hook.py:134`) ловятся
  и MCP-вызовы — условие `tool_name.startswith("mcp__")` рядом с `tool_name in TRACE_TOOLS`. Та же механика:
  `spanId="tool-"+toolUseId`, `tool` = полное имя `mcp__<server>__<tool>` (совместимость).
- **Поле `kind` в `tool.start` (только дозапись).** Значения `"mcp"` / `"bash"` / `"tool"` — чтобы фронт
  типизировал строки без зашитого списка имён. Для MCP дополнительно `server` и `mcpTool`. Парсинг имени —
  `_parse_mcp_name` (`scripts/telemetry_hook.py:195`): срез `mcp__`, затем `server,_,mcpTool =
  body.partition("__")` — разделитель сервер↔тул всегда `__`, имя сервера может содержать одиночные `_`
  (`plugin_ai-pathfinder_context7`). `arg` для MCP — первое непустое строковое значение `tool_input`,
  обрезка 200 (`_mcp_arg`, `scripts/telemetry_hook.py:210`): у MCP нет единого «ключевого поля».
- **Старый формат `tool.*` неизменен.** `kind`/`server`/`mcpTool` — НОВЫЕ поля, дозаписаны; порядок и
  состав старых полей `tool.start`/`tool.end` не тронуты (инвариант Langfuse-курсора), а
  `events_to_langfuse_batch` неизвестные `tool.*`-поля игнорирует.
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
- Текст читается строго в UTF-8 (`parse_transcript_messages`, `scripts/_aipf.py:362`).

### `GET /trace/actions?slug=<slug>&agent=<spanId|role>&session=<id>` — действия агента (ленивый, `agent-trace-details`)

- Вызывается **только** по явному раскрытию карточки агента в посмертной сводке. Читает **ОДИН** транскрипт
  агента и собирает достоверный по-агентный список действий (см. мост «спан→транскрипт» ниже).
- Метод `_trace_actions` (`scripts/server.py:394`), читалка `parse_transcript_actions`
  (`scripts/_aipf.py:451`), реюз `_resolve_transcript` (`scripts/server.py:466`). **Read-only** —
  `telemetry.cursor` не трогается. mtime-кэш ~3 с per-(slug, agent), т.к. транскрипт бывает большим.
- Контракт ответа:
  ```
  { "description": <string|null>,
    "actions": [ {"type","name","arg","status","ts","relMs"}, ... ],  // отсортировано по relMs
    "counts": {"tool","bash","mcp","subtask","hook"},
    "pending": <bool> }
  ```
  `type ∈ {tool, bash, mcp, subtask, hook}` (MCP по `name.startswith("mcp__")`, `Bash→bash`,
  `Task/Agent→subtask`, прочее→`tool`); `name` для MCP — `"<server> · <tool>"`. `status` = `ok`/`error` по
  наличию `tool_result`+`is_error`, иначе `running` (результата нет — прерван/в процессе). `relMs` — мс от
  первого действия. `pending:true` (нет транскрипта) → `actions=[]`, `counts`-нули — graceful degrade.
- `description` — «понятное описание задачи» агента из sidecar `meta.json` (`_agent_description`,
  `scripts/server.py:439`), либо `null`.

### `GET /trace?slug=<slug>` — посмертная модель

Спаны под-агентов + usage из транскриптов, агрегаты и гант. mtime-кэш 3 с. **Новое (`agent-trace-details`):**
каждая запись агента несёт `summary` — описание задачи. Для под-агента это `subagent.start.summary`
(=description), пробрасывается `_join_spans_transcripts → _agent_record(..., summary=sp.get("summary"))`
(`scripts/_aipf.py:776`, `:831`); оркестратор получает авто-подпись `"оркестратор сессии"`
(`scripts/_aipf.py:741`, решение q5=A — собственной «задачи» у него нет).

## Расположение транскриптов

- Главная сессия: `~/.claude/projects/<proj>/<sessionId>.jsonl`
- Под-агенты: `~/.claude/projects/<proj>/<sessionId>/subagents/agent-<agentId>.jsonl`
- `<proj>` — путь проекта с заменой разделителей на дефис
  (`c--Projects-personal-ai-pathfinder`). Локация — `find_main_transcript`/`find_subagent_files`
  (`scripts/_aipf.py:253`).
- Формат — JSONL, запись на строку: `type` (`user`/`assistant`), `timestamp`, `message.content[]` (блоки
  `text`/`tool_use`/`tool_result`), `message.usage`, `attributionAgent` (роль под-агента, только у его
  assistant-записей).

## Sidecar `agent-*.meta.json` и мост «спан → транскрипт» (`agent-trace-details`)

Рядом с каждым под-агентским транскриптом CC пишет sidecar
`~/.claude/projects/<proj>/<sessionId>/subagents/agent-<agentId>.meta.json` вида
`{agentType, description, toolUseId}`. Его `toolUseId` **точно совпадает** с `toolUseId` блока `Task` в
транскрипте оркестратора и со `spanId = "span-" + toolUseId` из `subagent.start` телеметрии. Это даёт
**детерминированный мост: спан под-агента (телеметрия) → его транскрипт → все его `tool_use` (включая
`mcp__*`)**, т.е. достоверную по-агентную атрибуцию действий **в обход ненадёжного lane** (lane из хука
остаётся best-effort, см. подводные камни и ADR-0003/ADR-0009).

- `find_subagent_meta(session_id)` (`scripts/_aipf.py:263`) — тот же glob, что `find_subagent_files`, но
  `*.meta.json`; возвращает `[{agentType, description, toolUseId}]` (UTF-8, терпит битый/отсутствующий файл).
- `_agent_description(slug, agent, session)` (`scripts/server.py:439`) — достаёт `description` по
  `meta.toolUseId == agent[len("span-"):]`, перебирая **все** session_id задачи.
- `parse_transcript_actions(path)` (`scripts/_aipf.py:451`) — читает `tool_use` (`name/id/input/timestamp`)
  и `tool_result` (`tool_use_id/is_error`) из транскрипта; статус ok/error/running — по наличию результата.
  Все `tool_use` в транскрипте под-агента принадлежат именно ему → атрибуция точна.
- **Предостережение:** `toolUseId` дрейфует между прогонами — мост надёжен в пределах одного прогона;
  при матчинге опираться на `toolUseId`, а не на позицию.

## Инварианты

- Хук пишет **ровно одну** строку на событие append-ом; любая ошибка → `exit 0`, воркфлоу не ломается.
- `/trace/feed`, `/trace/messages` и `/trace/actions` — **read-only**; курсор Langfuse (`telemetry.cursor`)
  ими не затрагивается.
- Курсор ленты — **байтовый оффсет** (`f.tell()`), не номер строки; переживает докатку строк в файл.
- `spanId` стабилен между `start` и `end` (`"tool-"+toolUseId` / `"span-"+toolUseId`).
- Новые типы событий добавляются **только** дозаписью; формат/порядок старых событий не меняется (иначе
  собьётся обогащение Langfuse `telemetry.enriched.json`).
- Парность Pre/Post **не гарантирована**: при прерывании инструмента `tool.start` останется без `end` —
  это валидное состояние `running`, UI/сервер обязаны его терпеть.

## Подводные камни

- **Атрибуция `tool.*` по агентам в ленте — best-effort (важно, нетривиально).** Из payload хука НЕЛЬЗЯ
  различить, какой под-агент выполнил `tool.*`: исполнителя в payload нет. Поэтому `lane` в ленте
  определяется **best-effort** (`_feed_lane`, `scripts/_aipf.py:595`): если в сессии открыт ровно один
  под-агентский спан — действие приписывается ему; иначе — `"orchestrator"`. При нескольких параллельных
  под-агентах атрибуция неточна. Это ограничение источника, не баг (ADR-0003).
- **Поправка к «один session_id у всех» (`agent-trace-details`).** Ранее доки/ADR-0003 утверждали, что
  оркестратор и все под-агенты делят ОДИН `session_id`. На практике **у одной задачи может быть несколько
  session_id** (напр. дочерняя сессия со своим top-level транскриптом и своим каталогом `subagents/`).
  Поэтому любой по-агентный агрегатор идёт по **ВСЕМ** session_id задачи (`build_trace`, `_agent_description`,
  `_resolve_transcript`). Это не отменяет best-effort lane: несколько параллельных под-агентов под одним
  session_id из payload по-прежнему не различить. **Точная атрибуция — только через транскрипты + `meta.json`**
  (мост выше, ленивый `/trace/actions`), а не через lane.
- **Кириллица в консоли ≠ повреждение файла.** Транскрипты и `telemetry.jsonl` корректны в UTF-8;
  «кракозябры» в консоли — это cp1251 stdout на Windows. Всегда читать файлы с `encoding="utf-8"`
  (`_iter_lines`, `parse_transcript_messages`), не полагаться на консольный рендер.
- **Матчер `.*` запускает хук на КАЖДЫЙ инструмент**, включая нетрейсимые (TodoWrite и т.п.) — они
  падают сквозь все ветки `build_event` и выходят без записи. Цена — лишние запуски `python3`; принятый
  компромисс (см. ADR-0002).
- **`tool_input` может быть не-dict или отсутствовать** — извлечение `arg` обязательно проверяет
  `isinstance(tool_input, dict)` (`_trace_arg`, `scripts/telemetry_hook.py:185`).
- **Атрибуция lane при `since>0`.** Если `subagent.start` под-агента остался до курсора, его tool-действия
  в дельте деградируют до `"orchestrator"`; клиент корректирует по своей накопленной модели (`build_feed`
  docstring, `scripts/_aipf.py:627`).
- **`/trace` и `build_trace` читают файл целиком** — это узкое место при росте `telemetry.jsonl`; именно
  поэтому живая лента вынесена в отдельный оффсетный `build_feed`, а не подмешана в `/trace`.

## Как расширять

- **Новый тип события телеметрии:** добавить ветку в `build_event` (`scripts/telemetry_hook.py:70`),
  только дозаписью новых полей; не менять старые. Если событие должно уходить в Langfuse — добавить маппинг
  в `events_to_langfuse_batch` (`scripts/_aipf.py`), иначе оно пропускается форвардером автоматически.
- **Новый инструмент в ленте:** добавить имя в `TRACE_TOOLS` и (опц.) поле в `_TRACE_ARG_FIELD`. MCP уже
  ловятся целым классом (`tool_name.startswith("mcp__")`) и типизируются полем `kind` — отдельно
  регистрировать каждый MCP-тул не нужно.
- **Новый тип действия в `/trace/actions`:** расширить классификатор `_action_type`
  (`scripts/_aipf.py:422`) и при необходимости карту `_ACTION_ARG_FIELD`; добавить новый ключ в `counts`
  (он же — легенда на фронте). Эндпоинт ленивый и достоверный — тяжёлое чтение транскрипта **не** класть на
  горячий путь `/trace/feed` (ADR-0001), новые трейс-данные по-агентно — отдельным ленивым эндпоинтом по
  образцу `_trace_actions`/`_trace_messages`, не утяжеляя `build_trace` (читает файл целиком).
- **Новое поле в delta-ленте:** дополнить запись в `build_feed` (`scripts/_aipf.py:673`) и потребление в
  `renderTrace` дашборда; помнить про дифф-рендер по `spanId` (не сбрасывать скролл/раскрытие).
- **Новый трейс-эндпоинт:** ветка в `do_GET` (`scripts/server.py:160`) + метод (по образцу `_trace_feed`);
  для живых данных — короткий кэш (≤1 с) или без кэша, slug валидировать `safe_slug`.

_updated: 2026-06-13_
