# Область: Параллельные запуски (worktree) и хаб всех задач

> Как несколько `/feature`-задач идут параллельно в отдельных git-worktree, при этом их артефакты
> попадают в ОДИН общий store, который видит единственный компаньон-сервер, а страница `/hub` даёт
> кросс-задачный обзор (активные / история / аналитика).

## Назначение

Раньше модель была «один сервер на `--root` → один общий `<root>/.workflow/tasks/`», и параллельные
feature-задачи дрались за рабочие файлы и ветку. Эта подсистема добавляет четыре вещи, не ломая
одиночный сценарий:

1. **Изоляция параллельной задачи в git worktree** — своя ветка и рабочее дерево, чтобы файлы не
   конфликтовали; артефакты при этом всё равно идут в общий store.
2. **Хаб-страница `/hub`** — обзор всех запусков (развитие лендинга, не вкладка дашборда).
3. **История** прошлых запусков с переходом в их дашборды.
4. Дешёвая **кросс-задачная аналитика** (событийная, без токенов).

## Модель: один store, много worktree

```
<main>/.workflow/tasks/<slugA>/  (state.json, dashboard.json, telemetry.jsonl, …)
<main>/.workflow/tasks/<slugB>/
        ▲
        │ symlink  <worktree>/.workflow ──▶ <main>/.workflow
        │
../pathfinder-worktrees/<slugB>/         ← рабочее дерево задачи B (ветка <slugB>)
        └ .workflow → (симлинк на общий store)

один сервер  python3 scripts/server.py --root <main>   видит ВСЕ задачи через _list_tasks()
```

- **Один сервер на проект.** Даже при нескольких параллельных задачах — ровно один сервер,
  укоренённый в main-репозитории. Хаб `/hub` перечисляет все запуски (см. `skills/feature/parallel.md`).
- **Один общий store.** Артефакты каждой задачи лежат в едином `<main>/.workflow/tasks/<slug>/`.
  Параллельная задача работает в своём worktree, но её артефакты попадают в общий store через симлинк
  `<worktree>/.workflow → <main>/.workflow` (создаёт `scripts/worktree.py`). `.workflow/` gitignored,
  поэтому без симлинка он был бы локален каждому worktree — см. ADR-0010.
- **Своя ветка и рабочее дерево.** Worktree даёт изолированное дерево и ветку (`<slug>` от `main` по
  умолчанию) — два дерева не конфликтуют по файлам.

## Ключевые файлы

### CLI worktree (`scripts/worktree.py`, новый)

stdlib-only хелпер (по образцу `scripts/server.py`); делит layout-хелперы с сервером и хуком через
`_aipf` (тот же sys.path-трюк). Подкоманды — `add`/`list`/`remove`.

- `scripts/worktree.py:51` — `main_root(start=None)`: корень main-репо **даже из worktree**, через
  `git rev-parse --git-common-dir` → родитель общего `.git`. Возвращает абсолютный путь или `None`.
- `scripts/worktree.py:69` — `worktree_dir(root, slug)`: сиблинг репо
  `<parent>/pathfinder-worktrees/<slug>` (никогда не внутри рабочего дерева → нет .gitignore-шума).
- `scripts/worktree.py:75` — `list_worktrees(root)`: парсер `git worktree list --porcelain` →
  `[{path, branch, head}]`; устойчив к сбою git (→ `[]`).
- `scripts/worktree.py:106` — `branch_exists(root, branch)`.
- `scripts/worktree.py:114` — `record_worktree_in_state(state, worktree_path, branch)`: **чистая**
  функция (без I/O и git) — выставляет `worktreePath`/`branch`/`updatedAt` в dict. Вынесена чистой
  ради offline-теста пути записи без реального worktree.
- `_write_state_fields(root, slug, worktree_path, branch)` (ориентир по имени функции) — читает
  `state.json`, ставит `worktreePath`/`branch`, пишет обратно. **Различает три исходных состояния файла
  через предчтение `os.path.exists` ДО `read_json`** (read_json graceful-дефолт не даёт отличить
  «нет файла» от «битый»):
  - **`merged`** — файл был валидным dict: поля дозаписаны в существующий state (обычный путь).
  - **`created`** — файла не было: минтит минимальный (`add` может опередить INTAKE на resume).
  - **`recovered`** — файл есть, но непарсимый/не-dict: **битый state.json НЕ затирается молча** — он
    отправляется в карантин через `_quarantine_corrupt_state` (переименование в
    `state.json.corrupt-<TS>`, где `<TS>` = `%Y%m%d-%H%M%S`+pid **без двоеточий** — NTFS запрещает `:`,
    `os.replace` атомарен и кросс-платформенен), печатается warning, затем минтится свежий минимальный.
  - **`overwritten`** — битый, но карантин не удался (`os.replace` упал): файл перезаписан минимальным,
    печатается честный warning «не удалось сохранить» (не ложный note, будто оригинал лежит рядом).
  Возврат — `(state, status:str)` со статусом из списка выше (раньше было `(state, created:bool)`).
  Единственный вызывающий `cmd_add` (`scripts/worktree.py:354`) печатает разный note по статусу.
  **`read_json` НЕ менялся** — graceful-контракт сохранён (на него опирается `test_corrupt_json_is_graceful`).
- `scripts/worktree.py:148` — `_ensure_workflow_symlink(main_root, wt_dir)`: создаёт симлинк, только
  если его нет; корректный симлинк не трогает (идемпотентный resume); **никогда не падает** — неудачный
  симлинк не должен срывать весь `add`.
- `scripts/worktree.py:174`/`:229`/`:279` — `cmd_add`/`cmd_list`/`cmd_remove`.

### Сервер: per-worktree diff и хаб (`scripts/server.py`)

- `scripts/server.py:56` — `HUB_ACTIVE_WINDOW_SEC = 24*3600`, `:57` — `HUB_TERMINAL_PHASES =
  {"DONE","ABORTED"}` — параметры критерия active/history.
- `scripts/server.py:537` — `_git(*args, cwd=None, timeout=10)`: добавлен необязательный `cwd` →
  `git -C (cwd or self.workspace.root)`. Старые вызовы не меняются.
- `scripts/server.py:550` — `_task_root(slug)`: рабочее дерево для diff задачи — `state.worktreePath`,
  если задан, существует и это git-дерево; иначе fallback на `self.workspace.root`. Путь
  валидируется, битый никогда не ломает страницу. Прокинут во **все** git-вызовы вкладки «Изменения»
  (`_build_changes` `:597`, `_changes_file` `:684`, `_base_commit` `:568`, `_is_noise`/`_count_lines`).
- `_hub()` (`scripts/server.py:855`): агрегат всех задач; кэш+лок (внешний TTL **3 c**, `now+3.0`,
  `:872`), мягкий дефолт `{"runs":[], "analytics":{}, "error":…}`. Read-only — `telemetry.cursor`/Langfuse
  не трогает.
- `_build_hub(now=None)` (`scripts/server.py:887`): обходит `_list_tasks()`, по задаче зовёт `_hub_run`;
  одна битая задача не топит агрегат (per-task try/except). В конце **прунит** из per-task кэша slug'и
  исчезнувших задач (`:903–907`, под `_hub_card_lock`). `now` дефолтится в `time.time()`; тест передаёт
  явно для детерминизма active/history.
- **Per-task mtime-кэш карточек** (`scripts/server.py:852–853`): класс-атрибуты
  `_hub_card_cache` (`slug -> {sig, card}`) + `_hub_card_lock`. См. блок «Per-task mtime-кэш хаба» ниже.
- `_stat_sig(path)` (`scripts/server.py:910`): дешёвая подпись файла `(st_mtime, st_size)` без чтения
  тела; `None` при `OSError`. `size` дополняет `mtime`, чтобы поймать append в ту же секунду на ФС с
  грубой гранулярностью (`telemetry.jsonl` append-only).
- `_hub_signature(slug)` (`scripts/server.py:921`): кортеж из трёх `_stat_sig` в фиксированном порядке —
  `telemetry.jsonl`, `state.json`, `dashboard.json`. Любое изменение любого из трёх флипает сигнатуру →
  пересборка.
- `_hub_run(slug, now)` (`scripts/server.py:933`): карточка задачи, **мемоизированная per-task по
  сигнатуре**. На хит сигнатуры сырая карточка переиспользуется дословно (без `read_json`, без прохода
  телеметрии); на промах — зовёт `_hub_build_card` и кладёт в кэш. Поле `active` **всегда**
  пересчитывается от `now` (см. инвариант). `os.stat` и тяжёлый билд — вне лока; под локом только
  чтение/запись кэша. Кэшированный dict **не мутируется** — возвращается копия `dict(card, active=…)`.
- `_hub_build_card(slug)` (`scripts/server.py:955`): сырая карточка **без** now-зависимого поля
  `active` — из `state.json` (авторитетно для phase/iteration/таймстемпов) + `dashboard.json`
  (title/status/progress) + лёгкий проход телеметрии. Зависит только от трёх файлов → кэшируема.
- `_hub_is_active(phase, updated, now)` (`scripts/server.py:1002`): критерий active/history (q7).
- `_hub_duration_ms(...)` (`scripts/server.py:1012`): длительность `updatedAt − createdAt` в мс.
- `_hub_telemetry(slug)` (`scripts/server.py:1020`): **один дешёвый проход** `telemetry.jsonl`
  (`_aipf._iter_lines`) — без транскриптов и `build_trace`: счётчики events/activity/subagents,
  множество session_id, first/last ts. Терпит битый/отсутствующий файл (нули). **Это и есть дорогая
  часть, ради которой введён сигнатурный кэш** — для неизменившихся задач не вызывается.
- `_hub_analytics(runs)` (`scripts/server.py:1067`): кросс-задачная аналитика (только событийная).
- `_median(values)` — медиана длительностей.
- `scripts/server.py:242` — маршрут `/hub.json` (без slug, особый случай как `/health`);
  `:244` — маршрут `/hub` (отдаёт `HUB_PAGE`).
- `scripts/server.py:1200` — константа `HUB_PAGE`: само-содержащий HTML (инлайн `<style>`+`<script>`,
  **без CDN**), `fetch('/hub.json')` + рендер трёх секций (вариант A); поллинг 3 c с диффом по
  сериализованному снимку (`tick()`), ошибки молча глотаются. Стиль зеркалит `templates/dashboard.html`.
- `scripts/server.py:1189` — ссылка «Открыть хаб всех запусков → /hub» в `INDEX_LANDING`.

### Атрибуция сессий (`scripts/_aipf.py`)

- `scripts/_aipf.py:19` — `SESSION_ID_RE = ^[A-Za-z0-9_-]+$` (анти-traversal при сборке пути).
- `scripts/_aipf.py:94` — `active_slug(root, session_id=None)`: порядок резолва — (0) per-session
  `.workflow/active/<session_id>.json` (только при валидном `session_id`), (1) общий `active.json`,
  (2) fallback на свежайший `state.json`. Без per-session файла поведение **точно как раньше**
  (back-compat одиночного сценария).

### Скилл и схема

- `skills/feature/parallel.md` (новый) — «когда/зачем/как» параллельных запусков для оркестратора:
  модель один store / много worktree, команды `worktree.py add/list/remove`, per-session `active.json`,
  ручная чистка после merge. Ссылка на него — из `skills/feature/SKILL.md` (INTAKE при параллельном
  запуске).
- `skills/feature/state-schema.md` — описаны новые необязательные поля `worktreePath`/`branch` и
  per-session файл `active/<session_id>.json`.

## Публичный интерфейс

### `GET /hub.json` — кросс-задачный агрегат (без slug)

Ответ: `{ "runs": [<карточка>...], "analytics": {…} }` (при ошибке — те же ключи + `"error"`; никогда
не 500). Карточка `runs[]` (`_hub_run`):

```
{ slug, title, kind, phase, status, iteration,
  awaiting,                       // bool — задача ждёт ответа человека (см. ниже)
  progress:{done,total},
  createdAt, updatedAt,
  worktreePath, branch,           // null для обычных (не-worktree) задач
  active,                         // bool — критерий active/history (см. ниже)
  subagents, sessions, events, activity,   // счётчики из одного прохода телеметрии
  firstTs, lastTs, durationMs }
```

**Поле `awaiting`** (`_hub_build_card`, `scripts/server.py:985`) — задача ждёт ответа человека (висит на
батч-гейте). OR-формула из двух источников: `state.checkpoint == "awaiting-batch"` **или**
`dashboard.status == "awaiting-batch"` (читаем оба — какой из артефактов опередил другой, тот и
сработает). Добавлено **дозаписью** (инвариант «только добавление»). Поле **косметическое** — влияет
только на отображение, **НЕ** на критерий active/history: `_hub_is_active` (`:1002`) не тронут, поэтому
терминальная задача, оставшаяся в `awaiting`, всё равно уезжает в «Историю» по своему `phase`/окну.

`analytics` (`_hub_analytics`, **только событийная, БЕЗ токенов/cost**):

```
{ total, active, done,
  phases:{<phase>:count, …},      // распределение, "—" для пустой фазы
  totalDurationMs, medianDurationMs,
  iterations, subagents, sessions, activity }
```

### `GET /hub` — хаб-страница

Отдаёт `HUB_PAGE` (`text/html`). Само-содержащий HTML без CDN; при загрузке `fetch('/hub.json')` и
рендерит **три секции** (вариант A, утверждён на гейте):

1. **Активные запуски** — карточки из `runs.filter(r=>r.active)`: slug/title, бейдж фазы,
   статус, прогресс, ветка/worktree, ссылка `/?slug=<slug>` (открыть дашборд задачи).
   - **Раскладка (2026-06-24):** один полноширинный столбец — `.cards { grid-template-columns:1fr }`,
     каждый запуск рисуется детальной карточкой `heroCard` (единый вид; деления hero/slim и `slimCard`
     больше нет). Шапка хаба `header.top` — полноширинная полоса, контент в обёртке
     `header.top .top-inner` (`max-width:1180px; margin:0 auto`) для паритета ширины со страницей
     задачи. Данные `/hub.json` остаются layout-агностичными.
   - **Бейдж awaiting.** `statusBadge` (`scripts/server.py:1440`) рисует для `awaiting-batch` бейдж
     **«⏳ ждёт ответа»** (раньше — «ждёт батч»), иначе «в работе». Зеркалит `.status.awaiting`
     дашборда.
   - **Подъём awaiting-карточек.** В `render()` (`:1518`) активные сортируются так, что задачи с
     `r.awaiting` всплывают наверх (`active.sort((a,b)=>(b.awaiting?1:0)-(a.awaiting?1:0))`) — то, что
     ждёт человека, видно первым.
   - **Опциональная подсветка карточки.** `runCard` вешает класс `.runcard.awaiting` (`:1475`) при
     `r.awaiting` → мягкая рамка/фон из `--awaiting-soft`/`--warn` (`:1333`). Косметика.
2. **История** — таблица (`histRow`) из `runs.filter(r=>!r.active)`: фаза/итерации/длительность/
   под-агенты/сессии/обновлено + ссылка на дашборд.
3. **Обобщённая аналитика** — счётчики (`stats`) + бары распределения по фазам (`phaseBars`); явная
   подпись «токены и стоимость не входят в кросс-задачный агрегат».

Деление active/history делается **на клиенте по полю `run.active`** (флаг считает сервер в `_hub_run`).
Поллинг каждые 3 c, дифф по `JSON.stringify(data)` (не перерисовывать впустую).

**Раскладка `render()` разбита на два узла** (`scripts/server.py:1543`/`:1548`): `#root` —
секция «Активные запуски», `#root-tail` — «История» + «Обобщённая аналитика». Между ними в разметке
сидит **третий, независимый** контейнер `#queue-root` (`scripts/server.py:1398`), который `render()`
**не трогает вообще** — его наполняет отдельный поллинг очереди (см. ниже). Так секция очереди
размещена визуально между «Активными» и «Историей», но дифф `/hub.json` не затирает её узел.

### `GET /queue.json` — очередь дренажа `/improve` (read-only passthrough, без slug)

Отдаёт содержимое `<workspace.base>/dispatch-queue.json` **дословно** (passthrough), контракт файла —
`skills/improve/dispatch-queue.md` (`mode:"sequential-feature"`, ADR-0014). Реализация —
`Handler._queue()` (`scripts/server.py:737`): `workspace.read_json(path, {"items": []})`.

- **Путь — через общий store (`workspace.base`, ADR-0010), не worktree-копию** — сервер всегда читает
  каноническую очередь main-репо, а не локальный снимок какого-то worktree.
- **Грейсфул:** нет файла / битый JSON → `{"items": []}` (`read_json` глотает
  `FileNotFoundError`/`JSONDecodeError`), эндпоинт **никогда не 500**.
- **Намеренно отдельный эндпоинт, мимо кэша `_hub`.** Очередь крошечная, но её **не** подмешивают в
  горячий `/hub.json`, чтобы не утяжелять его проход телеметрии и кэш (TTL 3 c). `done/total` и проценты
  считаются на клиенте (`renderQueue`).

Форма item (по контракту `dispatch-queue.md`, бэкенд агностичен к содержимому): `n`, `title`, `slug`,
`prism`/`candId`, `status ∈ {pending, in-progress, done, skipped, failed}` (+опц. `source` на верхнем
уровне). Сервер ничего не валидирует — отдаёт как есть.

#### Секция «Очередь /improve» в хабе

В `HUB_PAGE` — независимый под-узел `#queue-root` со своим поллингом `tickQueue()`/`renderQueue()`
(`scripts/server.py:1674`/`:1641`): свой `fetch('/queue.json')`, свой дифф по `JSON.stringify` (`lastQueue`),
свой `setInterval(3000)` — **полностью отвязан** от `tick()`/`render()` хаба. Если `items` пуст — узел
прячется (`renderQueue` пишет пустой `innerHTML`, плюс `#queue-root:empty { display:none }`), так что на
обычном дашборде без активной очереди секции нет.

Что показывает (табличный стиль): заголовок с прогрессом `done / total` + бар + мета `осталось N`;
подсветка строк `failed`/`skipped` (классы статусов собраны из существующих токенов обоих `:root`,
см. ADR-0015); ссылка `/?slug=<slug>` на дашборд фичи; **одна кнопка «Копировать команду дренажа»**
(`copyDrainCmd`, команда `DRAIN_CMD = "/loop /feature"`) через `navigator.clipboard` с
`execCommand`-fallback для небезопасного origin; toast подтверждения портирован из `dashboard.html`.

## Per-task mtime-кэш хаба (производительность пересборки `/hub.json`)

Пересборка `/hub.json` **мемоизируется per-task** по сигнатуре `(mtime, size)` трёх входных файлов
задачи. Это второй слой кэша **под** внешним TTL-кэшем `_hub` (`now+3.0`).

```
GET /hub.json
  └ _hub()            внешний TTL-кэш 3 c  (нижняя граница; ≥ интервала поллинга хаба)
      └ _build_hub()  обход _list_tasks() + прунинг исчезнувших slug
          └ _hub_run(slug, now)  ──► sig = _hub_signature(slug)   [3× os.stat, без чтения тел]
                 ├ sig совпала с кэшем → сырую карточку берём из памяти (НЕ читаем файлы)
                 └ sig изменилась      → _hub_build_card(slug)     [read_json + _hub_telemetry]
              затем active = _hub_is_active(phase, updatedAt, now)  ВСЕГДА пересчитывается
```

- **Сигнатура — три файла, фиксированный порядок** (`_hub_signature` `:921`): `telemetry.jsonl`,
  `state.json`, `dashboard.json` — ровно те, от которых зависит сырая карточка. `(st_mtime, st_size)`
  снимается без чтения тела (`_stat_sig` `:910`); `size` ловит append в ту же секунду на ФС с грубой
  гранулярностью mtime. Любое изменение любого файла флипает сигнатуру → перепарс.
- **Перепарс только изменившихся.** Дорогой проход `_hub_telemetry` (всё `telemetry.jsonl` задачи) +
  `read_json(state/dashboard)` идут **только для задач с изменившейся сигнатурой**. Неизменившиеся
  задачи отдают сырую карточку из `_hub_card_cache` без I/O по их телу. Горячий путь становится
  пропорционален числу **изменившихся** задач, а не всех (типично 1–2 активные).
- **`active` пересчитывается от `now` всегда.** Сырая карточка (`_hub_build_card` `:955`) намеренно
  **не содержит** поле `active` — оно зависит от стенных часов (окно `HUB_ACTIVE_WINDOW_SEC`, 24 ч) и
  должно отражать момент запроса, а не момент построения карточки. `_hub_run` дописывает его на каждый
  вызов: `dict(card, active=_hub_is_active(card["phase"], card["updatedAt"], now))`. Так задача,
  пересёкшая границу 24 ч **без записи в файлы**, корректно уезжает в историю, хотя её карточка из кэша.
- **Кэш не мутируется.** Возвращается копия (`dict(card, …)`), сырой объект в кэше остаётся чистым.
- **Прунинг.** `_build_hub` после обхода удаляет из кэша slug'и задач, которых больше нет в
  `_list_tasks()` (под `_hub_card_lock`) — кэш не растёт за счёт исчезнувших задач.
- **Лок-дисциплина.** `os.stat` и тяжёлый `_hub_build_card` идут **вне** `_hub_card_lock`; под локом —
  только чтение/запись `_hub_card_cache` (паттерн как у кэша `/trace`).
- **Контракт `/hub.json` не изменён** — те же поля карточки/аналитики; это чистая перф-оптимизация.

## Критерий active vs history (q7)

Задача **активна** (`_hub_is_active`, `scripts/server.py:1002`), если:
`phase ∉ {DONE, ABORTED}` **И** `updatedAt` свежее `HUB_ACTIVE_WINDOW_SEC` (24 ч). Иначе — история.

- Источник полей — `state.json` (надёжнее телеметрии: `phase`/`iteration` в событиях бывают `null`).
- Нет таймстемпа `updatedAt` → считаем **активной** (не прячем живой запуск без метки времени).

## Инварианты

- **Одиночный сценарий не ломается.** Без `worktreePath` в `state.json` — diff идёт по
  `self.workspace.root` (как раньше); без per-session `active/<sid>.json` — старое поведение
  `active_slug`. Поля добавлены **только дозаписью** (инвариант «только добавление», conventions.md).
- **Хаб read-only.** `_hub`/`_build_hub`/`_hub_run`/`_hub_build_card`/`_hub_telemetry` не пишут ничего
  на диск, `telemetry.cursor` и Langfuse не затрагивают — те же гарантии, что у `/changes` и
  трейс-эндпоинтов (`_hub_card_cache` — память процесса, не файлы). `_queue` — тоже чистое чтение
  (passthrough `dispatch-queue.json`).
- **`active` НЕ кэшируется (now-инвариант).** Per-task кэш хранит **сырую** карточку без поля `active`;
  оно пересчитывается от `now` на каждый вызов `_hub_run` (окно 24 ч). Никогда не отдаём `active` из
  кэша — иначе задача застряла бы в «активных» после пересечения окна без записи в файлы. Сырая карточка
  не мутируется (возвращается копия). См. блок «Per-task mtime-кэш хаба».
- **Сигнатура per-task кэша = `(mtime, size)` трёх файлов.** Карточка зависит только от
  `telemetry.jsonl`/`state.json`/`dashboard.json`; любое их изменение флипает сигнатуру и форсит
  перепарс. Не добавляй в карточку поле, зависящее от чего-то **вне** этих трёх файлов (или от `now`),
  не расширив сигнатуру / не вынеся пересчёт в `_hub_run` — иначе кэш отдаст устаревшее значение.
- **`/queue.json` мимо кэша `_hub`.** Очередь читается **отдельным** эндпоинтом, а не подмешивается в
  `/hub.json`, чтобы не утяжелять горячий проход телеметрии хаба. Секция очереди в `HUB_PAGE` —
  **независимый узел `#queue-root` со своим поллингом**, который `render()` не касается → дифф
  `/hub.json` не затирает очередь, а дифф `/queue.json` не дёргает карточки запусков.
- **`awaiting` — косметический флаг, не критерий active/history.** Бейдж/подъём/подсветка карточки
  завязаны на `run.awaiting` (OR `state.checkpoint`/`dashboard.status`), но деление active↔history
  по-прежнему делает только `_hub_is_active` по `phase`/окну. Терминальная awaiting-задача остаётся в
  «Истории» — флаг не удерживает её в активных.
- **Хаб не зовёт `build_trace`.** Аналитика и счётчики строятся одним проходом `telemetry.jsonl` без
  транскриптов: транскрипты дороги и **физически отсутствуют в worktree** (живут в `~/.claude/...`).
  Поэтому токенов/cost в кросс-задачном агрегате нет (см. ADR-0010).
- **Кэш+лок+мягкая деградация** у `_hub` (внешний TTL 3 c), per-task кэша карточек и `_changes` — не
  ломать; одна битая задача не топит агрегат. Внешний TTL — нижняя граница (поглощает залпы поллинга);
  per-task сигнатурный кэш экономит проход телеметрии **внутри** пересборки на промахе TTL.
- **`worktreePath` валидируется** в `_task_root` (существование + `rev-parse --is-inside-work-tree`)
  перед использованием как `cwd` для git — битый путь молча уходит в fallback.
- **`scripts/worktree.py` идемпотентен.** `add` на повторе переиспользует существующий worktree/ветку
  (resume), не падает; `record_worktree_in_state` перезаписывает поля теми же значениями.
- **Битый `state.json` не теряется молча.** `_write_state_fields` никогда не затирает непарсимый
  `state.json` без следа: оригинал уходит в карантин `state.json.corrupt-<TS>`, и только потом минтится
  свежий минимальный (статус `recovered`). Это защищает полузаписанный/затёртый INTAKE-снимок
  оркестратора (`phase`/`iteration`/`dispatched`/`questions`) от потери при гонке `add`↔INTAKE. Если
  карантин невозможен (`os.replace` упал) — статус `overwritten` с честным warning, а не ложный note.
- **`remove` НЕ удаляет историю.** `git worktree remove` + снятие симлинка, но
  `<main>/.workflow/tasks/<slug>/` остаётся — история видна в секции «История» хаба.

## Подводные камни

- **Сессию запускать ВНУТРИ каталога worktree.** Только тогда `cwd` телеметрийного хука резолвится
  через симлинк `<worktree>/.workflow → <main>/.workflow` в общий store. Запуск из main-дерева сломает
  атрибуцию — артефакты уйдут не туда. Это явно прописано в `skills/feature/parallel.md`.
- **Симлинки на Windows.** `os.symlink` на Windows требует прав/Developer Mode; `_ensure_workflow_symlink`
  ловит `OSError` и **не падает** (печатает warning), но без симлинка общий store не работает — на
  Windows может потребоваться запуск из-под админа/Developer Mode.
- **knowledge-граф переиспользует `_build_changes`.** `_build_knowledge` зовёт `_build_changes`, чтобы
  пометить touched-файлы; теперь `_build_changes` диффит **worktree** задачи, поэтому пути touched —
  относительно **worktree-дерева**, а не main. Контракт `f.path` (реальные относительные пути)
  сохранён (см. `areas/dashboard-changes-tab.md`), но корень другой — учитывать при сопоставлении с
  knowledge-узлами main-репо.
- **Per-session `active.json` обязателен при параллели.** Один общий `active.json` перезатирается
  конкурентными сессиями → `session.start/end` атрибутируются не той задаче. Per-session ключ
  (`active/<session_id>.json`) это чинит; оркестратор пишет его **дополнительно** к `active.json`.
- **`phase`/`iteration` в телеметрии бывают `null`** — поэтому критерий active/history и поля карточки
  берутся из `state.json`/`dashboard.json`, а не из событий.
- **Карантин ≠ авто-откат (известный долг).** `_write_state_fields` спасает оригинал в
  `.corrupt-<TS>`, но **не** восстанавливает поля из последнего валидного снимка — INTAKE надо
  прогнать заново. Авто-откат на last-good (`.bak` перед затиранием) — **отдельная задача**, не сделана.
- **Защищён только writer (известный долг).** Укреплён лишь `_write_state_fields` (деструктивный путь).
  Читатели `state.json` (`telemetry_hook`, `_hub_run`) сейчас не деструктивны (graceful через
  `read_json`), но симметрично укрепить их — отдельный follow-up.

## Как расширять

- **Новое поле карточки хаба:** дополнить `_hub_build_card` (`scripts/server.py:955`) — **не**
  `_hub_run` — если поле выводится из трёх входных файлов (тогда оно автоматически кэшируется и
  инвалидируется сигнатурой). Если поле зависит от `now` или чего-то **вне** трёх файлов — считать его в
  `_hub_run` поверх кэшированной карточки (как `active`), **не** класть в сырую карточку. Потребление —
  в `runCard`/`histRow` внутри `HUB_PAGE`; помнить про дифф-поллинг (`JSON.stringify`).
- **Новый входной файл, от которого зависит карточка:** добавить его `_stat_sig` в `_hub_signature`
  (`:921`), иначе его изменение не инвалидирует кэш.
- **Новая метрика аналитики:** считать её одним проходом телеметрии в `_hub_telemetry` или агрегатом в
  `_hub_analytics` — **не** тащить транскрипты/`build_trace` в хаб (дорого, нет в worktree). Токены —
  только лениво на дашборде конкретной задачи.
- **Сменить раскладку хаба:** правится только `HUB_PAGE` — данные `/hub.json` layout-агностичны,
  бэкенд трогать не нужно.
- **Другой критерий active/history:** менять константы `HUB_ACTIVE_WINDOW_SEC`/`HUB_TERMINAL_PHASES`
  и `_hub_is_active` (`scripts/server.py:1002`), а не фильтр на фронте.
- **Новая подкоманда `worktree.py`:** добавить парсер в `build_parser` (`scripts/worktree.py:321`) +
  `cmd_*`; держать stdlib-only и идемпотентность, git-вызовы — через `_git` (никогда не падает).
- **Новое поле/действие в секции очереди:** правится `renderQueue`/`queueRow` в `HUB_PAGE`
  (`scripts/server.py:1641`/`:1626`); поле item — это просто ключ из `dispatch-queue.json`, сервер его
  отдаёт passthrough (валидация и форма — на стороне контракта `skills/improve/dispatch-queue.md`). **Не**
  подмешивать очередь в `/hub.json` и **не** наполнять `#queue-root` из `render()` — оставить независимый
  поллинг `tickQueue`, иначе дифф `/hub.json` затрёт секцию.

_updated: 2026-06-25 (state-json-corrupt-recovery: `_write_state_fields` различает missing/corrupt/valid,
битый `state.json` уходит в карантин `state.json.corrupt-<TS>` вместо молчаливого затирания, возврат
сменён на `(state, status)` со статусами created/recovered/merged/overwritten)_
_updated: 2026-06-25 (hub-json-mtime-cache: per-task mtime-кэш карточек хаба — сигнатура `(mtime,size)` трёх файлов `telemetry.jsonl`/`state.json`/`dashboard.json`, перепарс только изменившихся, инвариант «`active` не кэшируется» — пересчёт от `now`, прунинг исчезнувших, внешний TTL 3 c как нижняя граница; обновлены ориентиры по именам функций `_hub`/`_build_hub`/`_hub_run`/`_hub_build_card`/`_hub_telemetry`/`_hub_is_active`. Предыдущее — awaiting-human-signal: косметический флаг awaiting + бейдж/подъём карточки)_
</content>
</invoke>
