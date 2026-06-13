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
- `scripts/worktree.py:130` — `_write_state_fields(...)`: читает `state.json`, ставит поля, пишет
  обратно; терпит отсутствие файла (минтит минимальный — `add` может опередить INTAKE на resume).
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
- `scripts/server.py:706` — `_hub()`: агрегат всех задач; кэш+лок (TTL 2 c), мягкий дефолт
  `{"runs":[], "analytics":{}, "error":…}`. Read-only — `telemetry.cursor`/Langfuse не трогает.
- `scripts/server.py:724` — `_build_hub()`: обходит `_list_tasks()`, по задаче зовёт `_hub_run`;
  одна битая задача не топит агрегат (per-task try/except).
- `scripts/server.py:740` — `_hub_run(slug, now)`: карточка задачи из `state.json` (авторитетно для
  phase/iteration/таймстемпов) + `dashboard.json` (title/status/progress) + лёгкий проход телеметрии.
- `scripts/server.py:782` — `_hub_is_active(phase, updated, now)`: критерий active/history (q7).
- `scripts/server.py:792` — `_hub_duration_ms(...)`: длительность `updatedAt − createdAt` в мс.
- `scripts/server.py:800` — `_hub_telemetry(slug)`: **один дешёвый проход** `telemetry.jsonl`
  (`_aipf._iter_lines`) — без транскриптов и `build_trace`: счётчики events/activity/subagents,
  множество session_id, first/last ts. Терпит битый/отсутствующий файл (нули).
- `scripts/server.py:833` — `_hub_analytics(runs)`: кросс-задачная аналитика (только событийная).
- `scripts/server.py:858` — `_median(values)`.
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
{ slug, title, phase, status, iteration,
  progress:{done,total},
  createdAt, updatedAt,
  worktreePath, branch,           // null для обычных (не-worktree) задач
  active,                         // bool — критерий active/history (см. ниже)
  subagents, sessions, events, activity,   // счётчики из одного прохода телеметрии
  firstTs, lastTs, durationMs }
```

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

1. **Активные запуски** — карточки (`runCard`) из `runs.filter(r=>r.active)`: slug/title, бейдж фазы,
   статус, прогресс, ветка/worktree, ссылка `/?slug=<slug>` (открыть дашборд задачи).
2. **История** — таблица (`histRow`) из `runs.filter(r=>!r.active)`: фаза/итерации/длительность/
   под-агенты/сессии/обновлено + ссылка на дашборд.
3. **Обобщённая аналитика** — счётчики (`stats`) + бары распределения по фазам (`phaseBars`); явная
   подпись «токены и стоимость не входят в кросс-задачный агрегат».

Деление active/history делается **на клиенте по полю `run.active`** (флаг считает сервер в `_hub_run`).
Поллинг каждые 3 c, дифф по `JSON.stringify(data)` (не перерисовывать впустую).

## Критерий active vs history (q7)

Задача **активна** (`_hub_is_active`, `scripts/server.py:782`), если:
`phase ∉ {DONE, ABORTED}` **И** `updatedAt` свежее `HUB_ACTIVE_WINDOW_SEC` (24 ч). Иначе — история.

- Источник полей — `state.json` (надёжнее телеметрии: `phase`/`iteration` в событиях бывают `null`).
- Нет таймстемпа `updatedAt` → считаем **активной** (не прячем живой запуск без метки времени).

## Инварианты

- **Одиночный сценарий не ломается.** Без `worktreePath` в `state.json` — diff идёт по
  `self.workspace.root` (как раньше); без per-session `active/<sid>.json` — старое поведение
  `active_slug`. Поля добавлены **только дозаписью** (инвариант «только добавление», conventions.md).
- **Хаб read-only.** `_hub`/`_build_hub`/`_hub_telemetry` не пишут ничего, `telemetry.cursor` и
  Langfuse не затрагивают — те же гарантии, что у `/changes` и трейс-эндпоинтов.
- **Хаб не зовёт `build_trace`.** Аналитика и счётчики строятся одним проходом `telemetry.jsonl` без
  транскриптов: транскрипты дороги и **физически отсутствуют в worktree** (живут в `~/.claude/...`).
  Поэтому токенов/cost в кросс-задачном агрегате нет (см. ADR-0010).
- **Кэш+лок+мягкая деградация** у `_hub` (TTL 2 c) и `_changes` — не ломать; одна битая задача не топит
  агрегат.
- **`worktreePath` валидируется** в `_task_root` (существование + `rev-parse --is-inside-work-tree`)
  перед использованием как `cwd` для git — битый путь молча уходит в fallback.
- **`scripts/worktree.py` идемпотентен.** `add` на повторе переиспользует существующий worktree/ветку
  (resume), не падает; `record_worktree_in_state` перезаписывает поля теми же значениями.
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

## Как расширять

- **Новое поле карточки хаба:** дополнить `_hub_run` (`scripts/server.py:740`) и потребление в
  `runCard`/`histRow` внутри `HUB_PAGE`; помнить про дифф-поллинг (`JSON.stringify`).
- **Новая метрика аналитики:** считать её одним проходом телеметрии в `_hub_telemetry` или агрегатом в
  `_hub_analytics` — **не** тащить транскрипты/`build_trace` в хаб (дорого, нет в worktree). Токены —
  только лениво на дашборде конкретной задачи.
- **Сменить раскладку хаба:** правится только `HUB_PAGE` — данные `/hub.json` layout-агностичны,
  бэкенд трогать не нужно.
- **Другой критерий active/history:** менять константы `HUB_ACTIVE_WINDOW_SEC`/`HUB_TERMINAL_PHASES`
  и `_hub_is_active` (`scripts/server.py:782`), а не фильтр на фронте.
- **Новая подкоманда `worktree.py`:** добавить парсер в `build_parser` (`scripts/worktree.py:321`) +
  `cmd_*`; держать stdlib-only и идемпотентность, git-вызовы — через `_git` (никогда не падает).

_updated: 2026-06-13_
</content>
</invoke>
