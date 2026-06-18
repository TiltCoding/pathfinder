# Журнал задач

> Append-only. Каждая задача воркфлоу оставляет запись: что и **зачем** изменено. История для будущих агентов.

<!-- Новые записи — сверху. -->

## 2026-06-18 — approve-button-submit-guard (фича 2/8 из очереди `improve-overall`)
- **Что:** Гард в `#btn-approve` (`templates/dashboard.html`): при непустой очереди правок
  (`draftItems.length`) клик «Утвердить план» больше не шлёт `approve-plan`, а показывает
  предупреждающий `toast()` и кратко подсвечивает `#btn-submit` (реюз существующего класса
  `.flash`/`@keyframes flashpulse`). Ранний `return` до отправки сигнала; легитимный путь (очередь=0)
  не затронут.
- **Зачем:** Закрывает частую ошибку единственного гейта — человек жал «Утвердить» без «Отправить» и
  получал ложный «План утверждён ✓», хотя выбор не уходил агенту (`draft.json` не READABLE).
  cand-1 из аудита `improve-overall`.
- **Решения человека:** q1 — без confirm на пустой выбор (только гард непустого черновика);
  q2 — существующий `toast()`; q3 — да, подсветка «Отправить».
- **Объём:** 0 правок `scripts/*` (чисто клиентская правка, бэкенд агностичен). ADR не нужен.

## 2026-06-16 — mockup-security-headers (фича 8/8 — очередь `/improve` дренирована полностью)
- **Что:** Defense-in-depth для `/mockup` (единственный путь с не-доверенным активным контентом):
  `X-Content-Type-Options: nosniff` + строгий CSP **только на /mockup**. `scripts/server.py`: `_send`
  получил append-only параметр `extra_headers=None` (`:152`); константы `MOCKUP_CSP`/`MOCKUP_SEC_HEADERS`
  (`:72`); `_serve_mockup` (`:338`) отдаёт их. sandbox-iframe и realpath+commonpath traversal-гарды НЕ тронуты.
- **CSP (q1=A, совместимый):** `default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline';
  img-src data:; font-src data:; base-uri 'none'; form-action 'none'` — режет всю сеть/внешние ресурсы
  (эксфильтрация), но разрешает инлайн. Строгий профиль брифа (без script-src) сломал бы 8 существующих
  mockup'ов с инлайн-`<script>`; sandbox их и так исполняет — главная угроза сетевая, её закрывает default-src.
- **Тест:** `tests/test_mockup_security.py` (4) — /mockup несёт nosniff+CSP, обычный ответ нет, 404 нет.
- **Проверка:** py_compile + 123→127 зелёные + независимый smoke (curl -i показал оба заголовка). Источник:
  cand-26 (доступность+безопасность, score 1.00). ADR нет.
- **Итог прогона `/improve` (improve-overall):** все 8 фич очереди дренированы и зелёные (44→127 тестов,
  +83 теста по ходу). Дренаж шёл через `/loop /feature`, по фиче в свежем контексте.

## 2026-06-16 — hub-search-filters (фича 7/8 очереди `/improve`)
- **Что:** Клиентский поиск + чипы-фильтры по `phase`/`kind` в хабе `/hub`. Только инлайн JS/CSS в строке
  `HUB_PAGE` (`scripts/server.py`), **0 правок серверной логики** — поля уже в `/hub.json`.
- **Ключевое (урок фичи 3):** тулбар — отдельный устойчивый `#filter-bar` МЕЖДУ `</header>` и `#root`, вне
  перерисовываемых `render()` узлов; состояние фильтра в модульных JS-переменных (`q`,
  `activePhases`/`activeKinds` Set, `lastData`), `render()` фильтрует через `matches(r)` ДО деления
  active/history (аналитику не фильтруем). Иначе `<input>` терял бы фокус на каждом 3-с тике.
- **Чипы динамические** из `data.runs` (q1=A); `kind===null` (/feature·/improve) → синтетический чип
  «feature/improve» (q2=A, KIND_FEAT); раскладка v2 (панель-карточка со сбросом «найдено N из M»);
  персист в `localStorage['hubFilter']` (q3=A); пустой результат → `.empty` «ничего не найдено».
- **Проверка:** py_compile + node --check (оба `<script>`) + 123 теста зелёные; **живой браузер**
  (temp-сервер): чипы динамические (ANSWER/DONE/IMPLEMENT/PROPOSE + ask/feature-improve), поиск «hub»
  сузил 4→1, баннер «найдено N из M · сбросить». Источник: cand-21 (DX/UX, score 1.33). ADR нет.

## 2026-06-16 — readme-development-section (фича 6/8 очереди `/improve`)
- **Что:** Задокументировано «как запускать» — раздел `## Development` в `README.md` (после `## Install`):
  команда тестов `python3 -m unittest discover -s tests`, локальный запуск сервера
  `python3 scripts/server.py --root "$(pwd)"` (порт/URL из `.workflow/server.json`, `/health` отдаёт
  `{ok,ts,pid,port}`, флаги `--port`/`--no-browser`/`--no-forward`), заметка про хуки/Langfuse-env.
- **Единообразие:** все 9 `tests/test_*.py` приведены к единой docstring-шапке «Run with:» (модульная +
  `discover`). Часть файлов раньше команды не имела вовсе (test_ask/test_improve_dispatch/test_server_health).
- **Makefile:** добавлен тонкий stdlib `Makefile` (`make test` → discover, `make serve` → server.py), без
  новых зависимостей (q1=A).
- **Зачем:** «как прогнать тесты / поднять сервер» было самым частым вопросом — ответ добывался чтением
  исходников. Источник: `/improve` cand-16 (DX, score 1.50), парная к фиче 1 (тесты тихих путей).
- **Объём:** только docs — прод-код и тела тестов не тронуты; полный прогон остался зелёным (123). ADR нет.

## 2026-06-16 — awaiting-human-signal (фича 5/8 очереди `/improve`)
- **Что:** Когда задача упирается в батч-гейт и **ждёт ответа человека**, это теперь видно и в **хабе**,
  и фоновым **сигналом на дашборде**. Менялись `scripts/server.py` + `templates/dashboard.html` +
  `tests/test_hub.py`.
  - **Хаб (`scripts/server.py`).** В карточку `_hub_run` (`:793`) добавлено **дозаписью** поле
    `awaiting = (state.checkpoint == "awaiting-batch") or (dash.status == "awaiting-batch")` (OR двух
    источников — какой артефакт опередил, тот и сработал). Поле **косметическое**: `_hub_is_active`
    (`:782`) **НЕ** тронут — терминальная awaiting-задача всё равно уезжает в «Историю» по своему
    `phase`/окну, флаг её в активных не удерживает. В `HUB_PAGE`: бейдж awaiting переформулирован
    «ждёт батч» → **«⏳ ждёт ответа»** (`statusBadge`, `:1440`); awaiting-карточки **всплывают наверх**
    в `render()` (`active.sort` по `r.awaiting`, `:1518`); опц. подсветка `.runcard.awaiting`
    (`:1475`, из `--awaiting-soft`/`--warn`).
  - **Дашборд (`templates/dashboard.html`).** Фоновый сигнал «твой ход», грейсфул, без серверных
    контрактов. Детект перехода `working→awaiting` через модуль-переменную `prevStatus` (`:528`/`:790`):
    **первый render задаёт базовый статус без сигнала** (анти-спам — уже-awaiting задача при открытии
    вкладки не уведомляет). При переходе **и** `document.hidden` **и** выданном разрешении → браузер-
    `Notification` (`:795`, в `try/catch`). **Title-бейдж** `"● ждёт — <title>"` (`setTitleBadge`,
    `:534`) — базовый канал без Notification-разрешения; `visibilitychange` снимает бейдж при возврате
    на вкладку (`:2343`). `requestPermission` запрашивается **на жесте** (клик approve/submit,
    `:1101`/`:1107`), не на старте. Подсветка `.actionbar.awaiting` (`:163`/`:803`) из `--warn-soft`/
    `--warn` в обеих темах. Всё грейсфул: нет API/разрешения → остаются title-бейдж и подсветка.
  - **Тесты `tests/test_hub.py` (`AwaitingFlagTest`, 4):** `state.checkpoint=="awaiting-batch"` →
    `awaiting==True`; `dashboard.status=="awaiting-batch"` → `True`; оба пусты → `False`; и ветка OR.
- **Зачем:** батч-гейт «задача ждёт правок человека» был виден только если открыт **конкретный**
  дашборд этой задачи и вкладка на переднем плане. Человек с несколькими параллельными запусками (или со
  свёрнутой вкладкой) не понимал, что от него ждут ответа. Хаб теперь подсвечивает/поднимает ждущие
  задачи, а дашборд фоном сигналит (уведомление + бейдж в заголовке вкладки). Пробел функциональности
  закрыт без новых контрактов: косметическое поле дозаписью в хабе + чисто клиентский сигнал на дашборде.
- **Именная шероховатость (зафиксирована в обеих area-доках):** дашборд красит из `--warn-soft`, хаб —
  из `--awaiting-soft`; **значения одинаковые** (`#fff7ed`/`#2a2113`), имена разные — осознанная
  шероховатость по ADR-0015, не баг.
- **Проверка:** полный прогон `python3 -m unittest discover -s tests` зелёный, **119 → 123**;
  `py_compile` сервера + `node --check` инлайн-скриптов (×4); smoke `/hub.json` — поле `awaiting` приходит
  булевым.
- **Источник:** очередь `/improve` (прогон `improve-overall`, кандидат `cand-20`), фича 5/8, призма
  пробелы функциональности, score 1.50. Бриф: `.workflow/tasks/awaiting-human-signal/brief.md`.
- **Доки:** дополнены `areas/parallel-runs-hub.md` (поле `awaiting`: OR-формула, дозаписью,
  косметическое — не влияет на active/history; бейдж/подъём/подсветка карточки) и
  `areas/dashboard-feedback-ui.md` (секция «Сигнал awaiting»: детект перехода, Notification/title/
  visibilitychange, requestPermission на жесте, actionbar-подсветка, токен-шероховатость).
- **ADR:** нет — решение **аддитивное** (косметическое поле дозаписью + клиентский сигнал, новых
  архитектурных развилок нет; хаб — ADR-0010, темизация/токены — ADR-0015).

## 2026-06-16 — dispatch-queue-hub-section (фича 4/8 очереди `/improve`)
- **Что:** Очередь дренажа `/improve` стала **видна в хабе** — новый read-only эндпоинт + секция в
  `HUB_PAGE`. Менялись `scripts/server.py` + `tests/test_hub.py`.
  - **`GET /queue.json` (`scripts/server.py:737`, `Handler._queue`).** Passthrough-чтение
    `<workspace.base>/dispatch-queue.json` через `workspace.read_json` с грейсфул-дефолтом
    `{"items": []}` (нет файла / битый JSON → **не 500**). Путь — через **общий store** (`workspace.base`,
    ADR-0010), не worktree-копию: сервер всегда читает каноническую очередь main-репо. **Отдельный**
    эндпоинт (маршрут `:250`), намеренно **мимо кэша `_hub`** — крошечную очередь не подмешивают в
    горячий `/hub.json`, чтобы не утяжелять его проход телеметрии.
  - **Секция «Очередь /improve» в `HUB_PAGE`.** Отдельный контейнер `#queue-root` (`:1398`) +
    **независимый поллинг** `tickQueue()`/`renderQueue()` (`:1674`/`:1641`): свой `fetch('/queue.json')`,
    свой дифф (`lastQueue`), свой `setInterval(3000)` — отвязан от `tick()`/`render()` хаба. **`render()`
    разбит на `#root` (Активные запуски) и `#root-tail` (История + Аналитика)** (`:1543`/`:1548`),
    `#queue-root` сидит **между** ними (секция в СЕРЕДИНЕ, табличный стиль). Прогресс `done/total` + бар,
    подсветка `failed`/`skipped`, ссылки `/?slug=`, **одна кнопка** «Копировать команду дренажа»
    (`DRAIN_CMD="/loop /feature"`, `copyDrainCmd` через `navigator.clipboard` + `execCommand`-fallback);
    toast портирован из `dashboard.html`. Пусто → секция прячется (`renderQueue` + `#queue-root:empty`).
    Статус-классы pending/skipped собраны из существующих токенов обоих `:root` (ADR-0015).
  - **Тесты `tests/test_hub.py` (`QueueEndpointTest`, 3):** passthrough заполненной очереди, нет файла
    (грейсфул `{"items": []}`), битый JSON (грейсфул).
- **Зачем:** очередь `/improve` (ADR-0014) жила только в `dispatch-queue.json` на диске — человек не
  видел прогресс дренажа (сколько фич сделано/в очереди/сбоев) и не имел под рукой команды дренажа.
  Секция в хабе закрывает этот пробел функциональности, ничего не ломая: отдельный read-only эндпоинт +
  независимый под-узел — горячий `/hub.json` и его кэш не задеты.
- **Ключевая архитектурная деталь (зафиксирована в area-доке):** секция очереди — **независимый узел
  `#queue-root` со своим поллингом** между `#root` и `#root-tail`, а не встроена в `render()`. Иначе
  дифф `/hub.json` (перерисовка `#root`/`#root-tail`) затирал бы очередь, а её собственный апдейт дёргал
  бы карточки запусков. Разъединённый поллинг = два независимых дифф-цикла.
- **Проверка:** полный прогон `python3 -m unittest discover -s tests` зелёный, **116 → 119**;
  `py_compile` сервера + `node --check` инлайн-скрипта `HUB_PAGE`; **живая браузерная проверка** — `/hub`
  отрендерил секцию (3/8) между активными и историей, статусы/прогресс/кнопка копирования работают.
- **Источник:** очередь `/improve` (прогон `improve-overall`, кандидат `cand-19`), фича 4/8, призма
  пробелы функциональности, score 1.50. Бриф: `.workflow/tasks/dispatch-queue-hub-section/brief.md`.
- **Доки:** дополнена `areas/parallel-runs-hub.md` (контракт `GET /queue.json`, секция «Очередь
  /improve», инвариант «`/queue.json` мимо кэша + независимый `#queue-root`/поллинг», пункт «как
  расширять»).
- **ADR:** нет — решение мелкое и аддитивное (отдельный эндпоинт + независимый под-узел вместо встройки
  в `render()`), новых архитектурных развилок не вводилось; механика очереди уже покрыта ADR-0014, хаб —
  ADR-0010. Зафиксировано в area-доке + этой записи.

## 2026-06-16 — preserve-dashboard-input-on-poll (фича 3/8 очереди `/improve`)
- **Что:** Незабленённый ввод на вкладке «Контент» теперь **переживает тик автополлинга** —
  чисто клиентская правка `templates/dashboard.html` (~24 строки, **без серверных контрактов**).
  - **`captureActiveInput()` (`templates/dashboard.html:939`) / `restoreActiveInput(snap)` (`:949`).**
    `capture` снимает снапшот **только активного (сфокусированного)** textarea внутри `#content` с ключом
    `data-answer` **или** `data-comment-variant` (value, `selectionStart/End`, факт фокуса); иначе `null`.
    `restore` находит узел по тому же стабильному data-ключу (через `cssesc`), переписывает прованным
    значением предзаполнение шаблона, возвращает фокус и каретку (кламп по длине). **Тихий выход**, если
    поле исчезло (агент удалил вопрос/вариант на лету) или фокус был не в `#content`.
  - **Врезка в `render()`:** `captureActiveInput()` **до** `$("#content").innerHTML` (`:844`/`:845`),
    `restoreActiveInput(__snap)` **после** `wireBlocks()` (`:847`). Образец — save/restore скролла из
    `renderChat`. **Skip-render не вводился** — реальные апдейты плана от агента доходят сразу.
- **Зачем:** `render()` зовётся из polling-петли (`loadDraft`/`rerender`); каждая перерисовка пересобирает
  `#content.innerHTML`, и активный, но ещё **не забленённый** ввод (свой ответ на вопрос, коммент к
  демо-варианту) терялся вместе с кареткой. Инвариант «сохранение по blur/Cmd+Enter» латает долговечную
  запись в draft, но не окно «текст набран — DOM пересобран». Capture/restore закрывает именно это окно.
- **Утверждённый параметр:** спасать **только активное поле** (q2=A) — по одному на перерисовку; остальные
  textarea и так перечитываются из draft.
- **Проверка:** `node --check` шаблона зелёный; полный прогон тестов **116** не сломан (правка вне Python).
- **Источник:** очередь `/improve` (прогон `improve-overall`, кандидат `cand-1`), фича 3/8, призма UX,
  score 1.50. Бриф: `.workflow/tasks/preserve-dashboard-input-on-poll/brief.md`.
- **Доки:** дополнена `areas/dashboard-feedback-ui.md` (capture/restore в «Ключевых файлах», врезка в
  `render()`, новый инвариант «активный ввод переживает перерисовку»).
- **ADR:** нет (мелкая клиентская правка, новых архитектурных развилок нет; реюз паттерна save/restore
  из `renderChat` и draft-контракта ADR-0008).

## 2026-06-16 — server-liveness-stale-detection (фича 2/8 очереди `/improve`)
- **Что:** Сервер дашборда теперь **достоверно различает живой сервер и stale `server.json`**, закрывая
  старую боль «мёртвый сервер + протухший `server.json`» (см. память проекта).
  - **`/health` self-report (`scripts/server.py:181`).** Эндпоинт теперь отдаёт `{ok, pid, port}` — раньше
    был голый health-чек. `port` берётся из класс-атрибута `Handler.server_port` (`scripts/server.py:123`),
    который `main` выставляет **после** `bind()` (`scripts/server.py:1731`) — реальный занятый порт, а не
    `--port`. Так клиент может сверить, что отвечающий сервер — именно тот, что записан в `server.json`.
  - **Чистые stdlib-функции детекта (новые, оффлайн).** `process_alive(pid)` (`scripts/server.py:1623`) —
    `os.kill(pid, 0)`; **defensive**: неизвестный/нечисловой pid = мёртв, а `PermissionError`/прочий
    `OSError` (вкл. Windows без поддержки) = «жив», чтобы из-за ограничения пробы **никогда не выбросить
    рабочий сервер**. `read_server_info(workspace)` (`:1648`) — читает `server.json`, **никогда не падает**
    (битый/отсутствующий → `None`). `server_info_is_stale(info, current_port=None)` (`:1660`) — stale, если
    нет pid / pid мёртв / `current_port` задан и не совпал с записанным.
  - **Старт `main` (`scripts/server.py:1728`).** Прежний `server.json` читается **до** bind; после bind при
    stale (мёртвый pid / несовпадающий порт) логируется `stale server.json … replacing` и `server.json`
    **безусловно перезаписывается** свежим pid/port.
  - **Reuse-контракт усилён в скиллах.** В `skills/{feature,improve,ask,new-product}/feedback-loop.md`
    reuse-проза дополнена: переиспользовать сервер только если `GET /health` отвечает **И** его `pid`/`port`
    совпадают с `server.json`, иначе считать stale и поднимать новый (раньше — «если `server.json` есть и
    `/health` отвечает», без сверки идентичности).
  - **Тесты `tests/test_server_health.py` (17, оффлайн без сокета)** — `process_alive` (живой/мёртвый/
    нечисловой/`pid≤0`/PermissionError-monkeypatch), `read_server_info` (битый/нет файла/не-dict),
    `server_info_is_stale` (все ветки, сверка порта), `/health`-пейлоад. По конвенции тестов из
    `conventions.md` (sys.path-хак, tempfile, без сети).
- **Зачем:** прежний контракт reuse доверял самому факту наличия `server.json` + ответу `/health`, но не
  проверял, что отвечает **тот же** сервер. После падения/рестарта оставался stale `server.json` (мёртвый
  pid, чужой порт), и агенты могли «переиспользовать» несуществующий сервер или конфликтовать по порту.
  pid/port в `/health` + сверка дают агенту способ отличить живой свой сервер от трупа в файле.
- **Проверка:** полный прогон `python3 -m unittest discover -s tests` зелёный, **99 → 116**. Реальный
  smoke подтвердил детект stale `server.json` + перезапись на старте.
- **Источник:** очередь `/improve` (прогон `improve-overall`, кандидат `cand-7`), фича 2/8, призма
  надёжность, score 1.67. Бриф: `.workflow/tasks/server-liveness-stale-detection/brief.md`.
- **Доки:** точечно дополнены `architecture.md` (сквозной механизм «startup-контракт сервера: `server.json`
  + stale-детект») и `integrations.md` (артефакт `server.json` с pid/port; `/health` self-report).
- **ADR:** нет (механика мелкая, аддитивная; новых архитектурных развилок не вводилось — контракт reuse
  усилён, а не пересмотрен).

## 2026-06-16 — tests-silent-critical-paths (фича 1/8 очереди `/improve`)
- **Что:** Добавлены **3 оффлайн stdlib-`unittest` файла** на «тихие критические пути» — **прод-код
  НЕ менялся** (чистые тесты). Прогон `python3 -m unittest discover -s tests` зелёный: было **44**
  теста, стало **99**.
  - `tests/test_langfuse_batch.py` (22 теста) — `events_to_langfuse_batch`/`langfuse_config_from_env`/
    `_envelope` (`scripts/_aipf.py`): детерминизм маппинга известных типов событий +
    **инвариант «неизвестный тип события (`turn.stop`/`tool.*`/новый) намеренно пропускается»** —
    защищает «безопасное добавление типов» (conventions.md «Только добавление в формат телеметрии»).
  - `tests/test_feed.py` (16 тестов) — `_iter_lines_from`/`build_feed` (`scripts/_aipf.py`): байтовый
    курсор, дельта-only/stateless (ADR-0001), at-least-once на незавершённом хвосте, «битая
    JSON-строка не роняет ленту» (мягкая деградация), lane best-effort (ADR-0003). Фикстуры — по форме
    `telemetry_hook.build_event`.
  - `tests/test_worktree.py` (17 тестов) — `build_parser` (чистый argparse), `_ensure_workflow_symlink`
    (идемпотентность), анти-traversal через `_aipf.safe_slug`, бонус `list_worktrees`
    (porcelain-парсер через monkeypatch `_git`). Git не вызывается.
- **Зачем:** эти пути отказывают **молча** (маппинг событий в Langfuse, чтение хвоста ленты, парсеры
  CLI), регрессия там незаметна на глаз. Тесты фиксируют контракты и инварианты как исполнимую
  спецификацию.
- **Источник:** очередь `/improve` (прогон `improve-overall`, кандидат `cand-14`), фича 1/8,
  призма DX, score 1.83. Бриф: `.workflow/tasks/tests-silent-critical-paths/brief.md`.
- **Зафиксированная конвенция:** оформлена секция **«Тесты»** в `conventions.md` (оффлайн `unittest`;
  `sys.path`-хак для импорта `scripts/`; tempfile+`addCleanup`; фикстуры `telemetry.jsonl` через
  `_aipf.task_file`; git — через monkeypatch `_git`; запуск `python3 -m unittest discover -s tests`).
  Конвенция уже фактически устоялась (7 тест-файлов с одинаковой шапкой) — описана, чтобы будущие
  агенты переиспользовали паттерн, а не изобретали.
- ⚠ **Наблюдение (НЕ баг этой задачи, прод не трогали — q4=A):** `_ensure_workflow_symlink`
  (`scripts/worktree.py:202`) сравнивает `os.path.realpath(link)` против **не-резолвнутого**
  `_aipf.workflow_base(main_root)` (`scripts/_aipf.py:41` симлинки не резолвит). На macOS, где tempdir
  `/var`→`/private/var`, это уводит идемпотентный путь в ветку warning, если пути не нормализовать.
  В тесте обойдено фикстурой (`realpath` на tempdir). Потенциальная хрупкость прод-сравнения — **возможный
  follow-up** (нормализовать обе стороны сравнения); ADR ради этого не заводил. Отражено в conventions.md
  (секция «Тесты», ⚠-нюанс).
- **Бриф:** `.workflow/tasks/tests-silent-critical-paths/brief.md` (план — там же; задача из очереди
  `/improve`, см. запись `improve-overall` ниже).
- **ADR:** нет (чистые тесты, новых решений не вводилось).

## 2026-06-15 — improve-overall (прогон `/improve`, диспетч в очередь)
- **Что аудировали:** сквозной аудит **всего приложения** ai-pathfinder через `/improve`
  (workflow-оркестратор; код проекта НЕ правился). Это не feature-задача — `/improve` производит
  feature-прогоны, а не редактирует код.
- **Форма рой → консенсус → выбор → диспетч** (механика — ADR-0012/0013/0014):
  - **Рой:** 7 read-only scout-аналитиков `wf-improver` (scout-режим) по призмам — UX/продукт,
    перформанс, надёжность/устойчивость, техдолг, DX, пробелы функциональности, a11y+безопасность →
    **49 сырых кандидатов**.
  - **Консолидация:** оркестратор дедуплицировал по «той же области/сути изменения» (смежные находки
    разных призм слиты, `areas` объединены) → **27 кандидатов** `cand-1…27`
    (`.workflow/tasks/improve-overall/candidates.md`, id стабильны).
  - **Панель:** 3 голосующих `wf-improver` (vote-режим) независимо оценили **весь** список 0–3 по
    imp/eff/rsk/conf → детерминированная агрегация оркестратором
    `score=(mean(imp)−0.5·mean(eff)−0.5·mean(rsk))·mean(conf)/3`, отброс `keep==0` → **топ-8**.
  - **Гейт:** человек отметил **все 8** как «Делаем».
  - **DISPATCH:** 8 фич поставлены в `.workflow/dispatch-queue.json` (`mode:"sequential-feature"`,
    ADR-0014), по `brief.md` на фичу в `.workflow/tasks/<slug>/`. `/improve` `/feature` сам не
    запускает (чистота контекста) — дренаж очереди отдельными `/feature`-прогонами.
- **Что попало в очередь (8 фич, n · slug · призма · score):**
  1. `tests-silent-critical-paths` · DX · 1.83 — тесты на `events_to_langfuse_batch`/`build_feed`/worktree CLI.
  2. `server-liveness-stale-detection` · надёжность · 1.67 — pid/port в `/health` + детект stale `server.json`.
  3. `preserve-dashboard-input-on-poll` · UX · 1.50 — не терять ввод textarea при innerHTML-перерисовке.
  4. `dispatch-queue-hub-section` · пробелы фич · 1.50 — `GET /queue.json` + секция очереди в хабе.
  5. `awaiting-human-signal` · пробелы фич · 1.50 — awaiting-флаг в хабе + Notification/title в дашборде.
  6. `readme-development-section` · DX · 1.50 — README «Development» + команда тестов.
  7. `hub-search-filters` · пробелы фич · 1.33 — клиентский поиск/фильтры в хабе.
  8. `mockup-security-headers` · безопасность · 1.00 — CSP + nosniff на `/mockup`.
  > Эти 8 — **только в очереди**, НЕ реализованы. Реализация будет отдельными `/feature`-прогонами,
  > каждый со своей записью в журнале.
- **Операционная деталь (draft без submit прочитан напрямую):** человек на гейте нажал «Утвердить план»
  **без** промежуточного «Отправить» — все 8 пиков жили только в `draft.json` (несабмиченный черновик,
  который сервер не отдаёт по HTTP — вне `READABLE_FILES`). Оркестратор прочитал их **напрямую с диска**
  (полные и однозначные) вместо переспроса про Submit. Прагматичное отклонение от документированного
  порядка Submit→Approve, оправданное файловым доступом оркестратора и однозначностью намерения.
  Зафиксировано **аддитивным уточнением к ADR-0013** (раздел «Уточнение (2026-06-15)») — отдельный ADR
  не заводил: механика `/improve` уже покрыта ADR-0012/0014, draft-контракт — ADR-0013.
- **План:** `.workflow/tasks/improve-overall/plan.md`
- **ADR:** уточнение к `decisions/ADR-0013-improve-feature-pick-reuse-zero-server.md` (нового ADR нет).

## 2026-06-15 — ask-command (v0.15.0)
- **Что:** Добавлена четвёртая команда-оркестратор **`/ask`** — лёгкий **read-only** режим
  «вопрос-ответ». По вопросу человека оркестратор спавнит мини-рой read-only `ask-researcher` (по граням),
  консолидирует дайджесты, **сам синтезирует** текстовый ответ + инфографику + схему процесса (через
  штатный `demo`/`mockups`-механизм) и держит **чат** для дальнейших вопросов. Стадии
  `INTAKE → RESEARCH → SYNTHESIZE → ANSWER → DONE`: **нет** гейта плана, **нет** IMPLEMENT/VERIFY; ничего
  в коде проекта не правит.
  - **Скилл `skills/ask/`** (ws1): `SKILL.md` (frontmatter с `description`, разведённым от
    `/feature`/`/improve`/`/new-product`; «You are the orchestrator of a read-only Q&A workflow — you
    never edit project code»; таблица под-агентов `ask-researcher`/`wf-documenter`; start/resume;
    operating rules; телеметрия), доменный `phases.md` (машина стадий + контракт `demo`-визуализаций +
    авто-DONE). Переносимые урезанные копии из `skills/improve/`: `dashboard-guide.md` (без §SELECT GATE),
    `feedback-loop.md` (центр — секция **Chat**, гейта/`approve-plan` нет), `state-schema.md` (+ask-поля),
    `knowledge-guide.md` (как есть). **Не копируются** `consensus.md`/`dispatch-queue.md`/`loop.md`/
    `parallel.md` — рой `/ask` без голосования/очереди.
  - **Под-агент `agents/ask-researcher.md`** (ws1): read-only (`tools: Read, Grep, Glob, Bash`, без
    Write/Edit; **без `model:`** — дефолт сессии, как `wf-*`). Покрывает **одну грань** вопроса, читает
    `INDEX.md` первым, возвращает **структурированный дайджест** под синтез (`## Ответ`/`## Опорные
    источники`/`## Шаги рассуждения`/`## Числа/связи`/`## Уверенность/пробелы`), пишет `research/<n>.md`.
    Не рисует HTML/SVG и никого не спавнит. Свой файл (а не реюз `wf-explorer`) — нужны машинно-парсимые
    секции (числа для инфографики, шаги для схемы).
  - **Визуализации + чат** (ws2): инфографика `mockups/infographic.html` + схема `mockups/process.svg`
    подаются `demo`-механизмом (`GET /mockup` в sandbox-iframe) — **0 правок сервера/HTML**; имена под
    `MOCKUP_RE`, файлы без CDN. Чат-петля `ANSWER` через `chat.jsonl` + сигнал `chat` + long-poll `/wait`;
    новый содержательный вопрос → новый мини-рой; авто-DONE ~24 ч.
  - **Хаб-метка `kind` + доки + версия** (ws3): ~4 строки в `scripts/server.py` (`_hub_run` прокидывает
    `state.get("kind")`; `runCard`/`histRow` рисуют бейдж типа; опц. CSS `.badge.kind`) — append-only поле,
    HTML дашборда не тронут. Тест `tests/test_ask.py` (`kind:"ask"` → `run["kind"]=="ask"`; без `kind` →
    отсутствует) + mockups-роут (`MOCKUP_RE`, Content-Type). README (раздел `/ask` + Layout),
    `plugin.json` (0.14.1 → **0.15.0**, keyword `ask`, расширён `description`).
- **Зачем:** четвёртый сценарий плагина — «как устроено / почему / где» с **визуальным** ответом и чатом,
  ничего не меняя в репозитории. MVP стоит на существующих контрактах: текст=`summary`/`planBlocks`,
  визуализации=`demo`/`mockups`, чат=`chat.jsonl`, попадание в хаб — автоматически. Единственная осознанная
  правка кода — бейдж `kind` в хабе (бриф требует «отличать ask от feature/improve»).
- **Ключевые решения:**
  - **`/ask` поверх существующего контракта** — 0 правок сервера/HTML кроме append-only бейджа `kind`;
    мини-рой read-only ресёрчеров по граням с консолидацией оркестратором (расширяет паттерн роя `/improve`,
    но **без голосования/очереди**); синтез/рисование — у оркестратора (инвариант ADR-0006 «субагенты не
    спавнят субагентов»); нетерминальная `ANSWER` + авто-DONE ~24 ч — **ADR-0016** (связь с
    ADR-0008/0013/0010/0012/0014).
- **Утверждённые параметры (зафиксированы):** хаб — Вариант B (`kind` + бейдж, q1); карточка визуализаций —
  реюз `demo` as-is (q2); синтезирует и рисует оркестратор (q3); мини-рой по граням с консолидацией (q4);
  инфографика HTML + схема SVG (q5); имя команды `/ask` (q6); `ask-researcher` без `model:` (q8).
- **План:** `.workflow/tasks/ask-command/plan.md`
- **ADR:** `decisions/ADR-0016-ask-readonly-qa-over-existing-contract.md`
- **Область:** `areas/orchestrator-skills.md` (расширена секцией про `/ask`)

## 2026-06-15 — dashboard-light-dark-theme (v0.14.0)
- **Что:** Дашборд (`templates/dashboard.html`) и хаб (`HUB_PAGE` в `scripts/server.py`) получили
  **пользовательское переключение темы** между двумя режимами Светлая / Тёмная (иконка-кнопка ☀️/🌙;
  до явного выбора — следуем системе).
  - **Модель:** `localStorage['theme']` хранит **явный выбор** `'light'|'dark'`; отсутствие ключа (или
    legacy `'system'`) = следуем системе; на `documentElement` ставится **разрешённый**
    `data-theme="light|dark"`; пока выбор не сделан — резолв через `matchMedia('(prefers-color-scheme: dark)')`
    + подписка на `change` (следует за ОС только пока `storedTheme()` пуст). `'system'` в storage **не** пишется.
  - **Триггер сменён с media на атрибут:** 5 блоков `@media (prefers-color-scheme: dark)` переписаны на
    `:root[data-theme="dark"]`, светлый `:root` → `:root[data-theme="light"]`.
  - **FOUC-bootstrap:** инлайн-`<script>` в `<head>` перед `<style>` (`templates/dashboard.html:9-21`,
    аналог `HUB_PAGE` `:1238-1252`) применяет тему до первой отрисовки; резолв продублирован.
  - **Контрол:** **иконка-кнопка** `.ghost.theme-btn#theme-btn` со `<span id="theme-icon">` (☀️/🌙) в
    шапке рядом с «Чат» (`:461-463`, CSS `:349-352`; хаб `scripts/server.py:1340`). Иконка отражает тему,
    клик флипает light↔dark. **Не** segmented `.seg`. Логика: `storedTheme()`/`resolveTheme()`/
    `applyTheme()`/`initTheme()` (`:529-557`; хаб `:1351-1368`).
  - **Токены ошибки:** введены `--err`/`--err-soft` в обе темы (в тёмной красный светлее, `#f87171`),
    заменили ~11 повторов `#ef4444`; `.status.awaiting` → soft-токен варнинга.
  - **Хаб `/hub`** темизирован тем же подходом и **тем же ключом** `localStorage['theme']` → сквозной
    выбор темы хаб↔задача; палитры хаба и дашборда синхронизированы.
- **Зачем:** тёмная тема существовала только как системный media-оверрайд — пользователь не мог
  выбрать тему. Тема — чисто клиентская (сервер/контракты не тронуты).
- **Ключевые решения:** переключение через `data-theme`-атрибут + localStorage явного выбора `light|dark`;
  «как в системе» — дефолтом через состояние «выбор не сделан» (vs оставить media / класс на body /
  серверная тема / третий режим `'system'` на `.seg` — отвергнуты). Следствие: тёмный блок **не наследует**
  светлый → полнота тёмной палитры обязательна (добавлены ранее не-переопределявшиеся токены);
  FOUC-bootstrap дублирует резолв; общий ключ для хаба и дашборда; именная шероховатость `--warn-soft`
  (дашборд) vs `--awaiting-soft` (хаб) — **ADR-0015**.
- **Не тронуто (by design):** ролевая палитра таймлайна `ROLE_COLORS`/`roleColor` (ADR-0011), песочные
  `iframe.mock`, `color:#fff` на цветных подложках.
- **Поправка (v0.14.1):** исправлено с **3-режимного segmented** (Светлая/Тёмная/Системная на `.seg`) на
  **2-режимную иконку-кнопку** (Светлая/Тёмная) под фактический выбор человека (Вариант A). Причина дрейфа:
  выбор человека на гейте (A + 2 режима) остался **неотправленным черновиком** (`draft.json`) — до
  оркестратора дошёл только голый `approve-plan`, поэтому первая сборка пошла по рекомендованным дефолтам.
  Вскрылся **UX-баг дашборда:** approve с непустым draft молча теряет выбор. Доки приведены в соответствие.
- **План:** `.workflow/tasks/dashboard-light-dark-theme/plan.md`
- **ADR:** `decisions/ADR-0015-theme-toggle-data-attribute-localstorage.md`
- **Область:** `areas/dashboard-theming.md`

## 2026-06-13 — improve: DISPATCH → последовательная очередь `/feature` (v0.13.0)
- **Что:** Переделана модель DISPATCH у `/improve`. Было — **посев каждой выбранной фичи в свой git
  worktree + ручной запуск** человеком (seed-and-handoff). Стало — **очередь
  `.workflow/dispatch-queue.json` + последовательный дренаж через `/feature`, каждая фича в свежем
  контексте**. См. [ADR-0014](decisions/ADR-0014-improve-sequential-feature-queue.md).
- **Зачем:** на реальном прогоне `improve-runtime` (8 пиков) выяснилось, что запускать 8 worktree
  вручную невозможно, а «одна сессия делает все 8» раздувает контекст. Очередь + ре-инвок `/feature`
  даёт полное качество `/feature` на каждой фиче при чистом контексте.
- **Как:** `/improve` DISPATCH пишет только `brief.md` + `pending`-item в очередь (без worktree, без
  per-feature state/dashboard, без запуска `/feature` из своей сессии). `/feature` получил **queue-mode**:
  без явной задачи берёт младший `pending`, бриф уже готов (пропуск INTAKE), гонит полный workflow, на
  DONE помечает `done` и просит `/clear` + `/feature` (или `/loop /feature`).
- **Файлы:** `skills/improve/{SKILL,phases,consensus,state-schema}.md`, новый
  `skills/improve/dispatch-queue.md` (контракт очереди), `skills/feature/{SKILL,phases}.md`,
  `.claude-plugin/plugin.json` (0.12.0→0.13.0). Решение выбора `feat-K` и агрегация (ADR-0012/0013) —
  без изменений. Параллельный worktree-фан-аут остаётся opt-in (ADR-0010).

## 2026-06-13 — improve-workflow
- **Что:** Добавлена третья команда-оркестратор **`/improve`** — производитель feature-прогонов (не
  редактор кода). Стадии `INTAKE → SCOUT → CONSENSUS → PROPOSE/SELECT GATE → DISPATCH → DONE`: рой
  аналитиков обследует приложение → консенсус голосующей панели → человек выбирает фичи → посев
  параллельных `/feature`-прогонов в git-worktree (seed-and-handoff).
  - **Скилл `skills/improve/`** (ws1): `SKILL.md` (frontmatter с `description`, разведённым от
    `/feature`/`/new-product`; mental model; таблица под-агентов; start/resume; operating rules;
    телеметрия), доменные `phases.md` (машина стадий) и `consensus.md` (рой → дедуп → vote-панель →
    детерминированная агрегация → seed-and-handoff). Переносимые копии из `skills/feature/`:
    `feedback-loop.md`/`parallel.md`/`knowledge-guide.md` — почти дословно; `state-schema.md`
    (+секция «improve-specific fields») и `dashboard-guide.md` (+секция «SELECT GATE — контракт `feat-K`»).
  - **Под-агент `agents/wf-improver.md`** (ws2): read-only (`tools: Read, Grep, Glob, Bash`, без `model:`),
    **два режима** scout/vote, различаемые **промптом** оркестратора. SCOUT — кандидаты по призме (схема
    `### cand:`); VOTE — независимая оценка всего списка 0–3 (`### cand-K`). Один файл на два режима —
    следствие «model глобальна для subagent_type».
  - **SELECT GATE + DISPATCH** (ws3): выбор фич через контракт `feat-K` (карточка `planBlocks` ↔
    `questions[choice]` с тем же id, `options:["Делаем","Пропускаем"]`) + сигнал `approve-plan`, **0 правок
    `server.py`/`dashboard.html`**; DISPATCH реюзит `scripts/worktree.py` и хаб (`/hub`) без правок.
    (Тест `tests/test_improve_dispatch.py`, eval-кейс, рубрика `evals/rubrics/improve-quality.md` и бамп
    `plugin.json` 0.11.0 → **0.12.0** пишет поток ws3 — задокументировано здесь по плану b6.)
- **Зачем:** третий сценарий плагина — превратить «надо что-то улучшить» в приоритизированный,
  выбранный человеком набор feature-задач. «Консенсус роя» реализован честно: независимые голоса панели +
  детерминированная агрегация оркестратором (субагенты не спавнят субагентов; один LLM-агрегатор был бы
  невозможен и нечестен). Главный гейт — поверх существующего контракта (0 правок сервера/HTML). Реальный
  автозапуск N независимых Claude Code-сессий недоступен → seed-and-handoff: оркестратор готовит почву,
  человек заходит в каждый worktree и запускает `/feature`.
- **Ключевые решения:**
  - **Механика консенсуса** — рой 7 scout по призмам → консолидация+дедуп оркестратором (`cand-1…N`) →
    панель 3 vote → детерминированная агрегация (`score=(mean(imp)−w_e·mean(eff)−w_r·mean(rsk))·mean(conf)/3`,
    дефолты `w=0.5`, «согласие»=доля keep, топ-K 6–8). Почему панель + арифметика оркестратора, а не
    «консенсус в одном агенте» — **ADR-0012** (расширяет паттерн судейской панели ADR-0006/0007).
  - **Reuse-first выбор фич / 0 правок сервера** — контракт `feat-K` через `questions[choice]`+`approve-plan`,
    дефолт «нет ответа=Пропускаем», обязательный порядок Submit→Approve (`draft.json` не READABLE).
    Развилка A (0 правок, выбран) vs B (аддитивный чеклист в HTML, оставлен как апгрейд) — **ADR-0013**
    (связь с ADR-0008).
- **Утверждённые параметры (зафиксированы):** Вариант A для гейта (q1); 7 призм scout / 3 голосующих /
  топ-K 6–8 (q2); посев в `EXPLORE`/`working` (q3); одна модель `wf-improver` на оба режима (q4); дефолт
  «нет ответа = Пропускаем» (q5); лёгкая eval-фикстура (q6).
- **План:** `.workflow/tasks/improve-workflow/plan.md`
- **ADR:** `decisions/ADR-0012-improve-consensus-panel-deterministic-aggregation.md`,
  `decisions/ADR-0013-improve-feature-pick-reuse-zero-server.md`
- **Область:** `areas/orchestrator-skills.md` (расширена секцией про `/improve`)

## 2026-06-13 — rework-agent-timeline
- **Что:** Переписан таймлайн параллелизма сабагентов на вкладке «Трейсинг» под **Вариант C** —
  целиком на клиенте, в `renderGantt(host, subs, tr)` (`templates/dashboard.html`), **без правок сервера**.
  - **Две зоны в одном `section.card`:** (1) обзор всего прогона `[T0..T1]` — inline-`<svg>`
    **token-rate area chart** (высота = темп производства токенов, не число агентов) + полоса фаз +
    ось `HH:MM` UTC с делениями + **brush** для выбора окна; (2) детальная зона выбранного окна
    `[bs..be]` — greedy lane-packing, бары-`<div>` в пикселях («вместить окно в ширину карты»),
    цвет = роль, фон = регионы фаз.
  - **Фикс корневого бага палитры (b1):** новые хелперы `roleKey`/`roleLabel` рядом с
    `roleColor`/`ROLE_COLORS` — роль приходит с namespace-префиксом `ai-pathfinder:wf-*` и
    промахивалась мимо словаря (почти все бары были хеш-цветными). `roleKey` срезает префикс и `wf-`.
  - **Фазы (b14):** `phase` из `subs[]` рисуется как фоновые регионы + полосы-заголовки (фон=фаза,
    цвет бара=роль), бакет «вне фазы» для `null`.
  - **Поля `/trace`, использованные таймлайном:** `startTs/endTs`, `durationMs`, `out`, `phase`,
    `role`, `summary`, `spanId`, `ok` (+ `model`/`contextPct`/`costUsd` в тултип); границы окна — из
    готового `tr.timeline.{t0,t1}`. Сервер (`build_trace`/`_agent_record`/`scripts/server.py`) не тронут.
- **Зачем:** на длинном прогоне (десятки агентов × часы) старый гант не читался — бары сжаты в ширину
  карты, ось из двух меток, `fmtDur` без часов, палитра промахивалась. Вариант C даёт обзор-целиком +
  зум окном без горизонтального скролла; обзор по токенам честнее показывает «где сожгли работу».
- **Ключевые решения:** обзор по производству токенов (`rate=out/durationMs`, агрегация по бакетам —
  аппроксимация, т.к. `out` — итог, не временной ряд); разбивка на фазы (с честной оговоркой о
  разрежённости `phase`: 26/36 `null` в тест-датасете, т.к. фаза = `state.json.phase` на момент
  `subagent.start`; `workstream` хук не пишет вообще); нормализация `roleKey`; подводный камень
  `sig`-гарда (интерактив/brush — вне summary-DOM) — **ADR-0011**.
- **Доки:** обновлён `areas/telemetry-tracing.md` (раздел «Таймлайн «Трейсинг» — Вариант C»: поля
  `/trace`, нормализация роли, `sig`-гард). 0 правок сервера → доки серверного контракта не трогались.
- **План:** `.workflow/tasks/rework-agent-timeline/plan.md`
- **ADR:** `decisions/ADR-0011-timeline-variant-c-token-rate-phases.md`
- **Область:** `areas/telemetry-tracing.md`
- **Статус финала:** ✅ DONE. Реализовано и проверено в VERIFY. Фактические хелперы рендера:
  `drawOverview()`/`drawDetail()` (две зоны), `bindBrush()` (drag→окно, dblclick→весь прогон),
  `tokTotalWin()` (Σ токенов окна), `tickStepMs()` (шаг оси), `roleKey()`/`roleLabel()`,
  `fmtDurH()`/`clock()`, `PHASE_META`/`PHASE_ORDER`/`PHASE_NONE`; новый стабильный контейнер
  `#trace-gantt` (в `ensureTraceShell`), вынесенный из `#trace-summary`, чтобы 4-сек тик не сбивал
  brush; тултип — `#g-tip` (`position:fixed`, делегаты биндятся один раз). Проверка: тесты 26/26,
  `node --check` шаблона OK, XSS-аудит чист, живой рендер на 42 агентах/4.4 ч (обе темы), brush зумит
  и переживает перерисовку. `/code-review` (medium) — 0 подтверждённых багов.

## 2026-06-13 — parallel-runs-hub
- **Что:** Параллельные `/feature`-задачи в git worktree + хаб всех запусков.
  - **CLI `scripts/worktree.py` (новый, b1).** Подкоманды `add`/`list`/`remove` (stdlib-only):
    `add <slug>` создаёт worktree `../pathfinder-worktrees/<slug>/` на ветке `<slug>` от `main`,
    симлинкует `<worktree>/.workflow → <main>/.workflow` (`_ensure_workflow_symlink`) и пишет
    `worktreePath`/`branch` в `state.json` (`record_worktree_in_state` — чистая, тестируемая без git).
    Идемпотентен (resume переиспользует worktree/ветку). `main_root` через
    `git rev-parse --git-common-dir` работает и из worktree. `remove` чистит worktree+симлинк, но
    **не** трогает `.workflow/tasks/<slug>/` (история остаётся).
  - **Per-worktree diff (b6).** `_git(*args, cwd=None)` (`scripts/server.py:537`) и новый
    `_task_root(slug)` (`:550`): вкладка «Изменения» диффит `git -C state.worktreePath` (валидируется),
    fallback на main без поля. Прокинуто в `_build_changes`/`_changes_file`/`_base_commit`/`_is_noise`.
  - **Хаб (b5/b7/b8/b9/b10).** `GET /hub.json` (`_hub`/`_build_hub`/`_hub_run`, `scripts/server.py:706`)
    — кросс-задачный агрегат `{runs, analytics}` по `_list_tasks()`, кэш+лок+мягкая деградация,
    read-only. `_hub_telemetry` — **один проход** `telemetry.jsonl` (без транскриптов/`build_trace`).
    Критерий active/history `_hub_is_active` (q7): `phase ∉ {DONE,ABORTED}` И `updatedAt`<24ч
    (`HUB_ACTIVE_WINDOW_SEC`/`HUB_TERMINAL_PHASES`). `GET /hub` (`HUB_PAGE`) — инлайн-HTML без CDN, три
    секции Активные/История/Аналитика (вариант A), поллинг 3 c; ссылка на хаб в `INDEX_LANDING`.
  - **Per-session `active.json` (b3).** `active_slug(root, session_id)` (`scripts/_aipf.py:94`) сначала
    читает `.workflow/active/<session_id>.json` (`SESSION_ID_RE`, анти-traversal), затем `active.json`,
    затем свежайший `state.json`. Без per-session файла — старое поведение.
  - **Скилл/схема (b2/b4):** новый `skills/feature/parallel.md`, поля `worktreePath`/`branch` и
    per-session файл описаны в `skills/feature/state-schema.md`.
- **Зачем:** `.workflow/` gitignored → в worktree локален и пуст → хаб не видел бы чужие задачи.
  Симлинк сводит артефакты всех worktree в ОДИН store, который читает единственный сервер; `worktreePath`
  в state даёт серверу рабочее дерево для diff; per-session `active.json` чинит атрибуцию при общем
  store. Аналитика событийная (без токенов): транскрипты дороги и физически отсутствуют в worktree.
- **Ключевые решения:** симлинк (vs env vs реестр), `worktreePath` в state дозаписью, per-session
  `active.json`, токены вне кросс-задачного агрегата, отдельная страница `/hub` (не вкладка) — ADR-0010.
- **Дрейф доков:** заодно задокументировано недокументированное событие `phase` (пишет оркестратор, не
  хук; форвардится в Langfuse веткой `phase`/`gate`) в `areas/telemetry-tracing.md`.
- **План:** `.workflow/tasks/parallel-runs-hub/plan.md`
- **ADR:** `decisions/ADR-0010-shared-store-symlink-worktree.md`
- **Область:** `areas/parallel-runs-hub.md`

## 2026-06-13 — agent-trace-details
- **Что:** Вкладка «Трейсинг» детализирована по агентам. Бэкенд (`scripts/telemetry_hook.py`,
  `scripts/_aipf.py`, `scripts/server.py`; новый `tests/test_telemetry_actions.py`):
  - **MCP в ленте (b1).** В `tool.*`-ветку `build_event` (`scripts/telemetry_hook.py:134`) добавлен захват
    `mcp__*` той же механикой (`spanId="tool-"+toolUseId`). В `tool.start` дозаписаны НОВЫЕ поля: `kind`
    (`mcp`/`bash`/`tool`), для MCP ещё `server`/`mcpTool` (`_parse_mcp_name`, разделитель `__`; `arg` —
    первое строковое значение input, обрезка 200). Старый формат/порядок `tool.*` не тронут.
  - **Описание задачи в `/trace` (b2).** `_agent_record(..., summary=...)` (`scripts/_aipf.py:776`) кладёт
    `summary` в запись агента; под-агент — `subagent.start.summary`, оркестратор — авто-подпись
    `"оркестратор сессии"` (q5=A).
  - **Ленивый `GET /trace/actions?slug&agent&session` (b3).** `_trace_actions` (`scripts/server.py:394`),
    читалка `parse_transcript_actions` (`scripts/_aipf.py:451`), мост спан→транскрипт через sidecar
    `agent-*.meta.json` (`find_subagent_meta` `:263`, `_agent_description` `scripts/server.py:439`). Читает
    ОДИН транскрипт по раскрытию, read-only, mtime-кэш ~3 с. Контракт `{description, actions, counts,
    pending}`, идёт по ВСЕМ session_id задачи.
  - **Фронтенд (b4/b5)** правился параллельно: посмертная карточка агента стала раскрываемой (описание +
    две ленивые секции «Действия»/«Сообщения агента», единая хронология v2), живая лента типизирует
    MCP-строки `server · tool`.
- **Зачем:** lane из хука best-effort и не даёт достоверной по-агентной сводки (исполнителя нет в payload).
  Exploration вскрыл дрейф знаний: sidecar `meta.json.toolUseId` == `spanId` под-агента даёт
  **детерминированный** мост спан→транскрипт→его `tool_use` (вкл. MCP) в обход lane; у одной задачи бывает
  несколько session_id (поправка к ADR-0003). Достоверный список вынесен в ленивый эндпоинт, чтобы не
  грузить горячий путь ленты (ADR-0001) и не утяжелять `build_trace`.
- **Ключевое решение:** точная атрибуция через транскрипт + `meta.json`, новый ленивый `/trace/actions`
  вместо расширения `/trace`, MCP — дозаписью полей `kind/server/mcpTool` (ADR-0009, поправка к ADR-0003).
- **План:** `.workflow/tasks/agent-trace-details/plan.md`
- **ADR:** `decisions/ADR-0009-transcript-attribution-and-actions-endpoint.md`
- **Область:** `areas/telemetry-tracing.md`

## 2026-06-13 — dashboard-feedback-enhancements
- **Что:** Во вкладку «Контент» дашборда (`templates/dashboard.html`) добавлены две возможности
  обратной связи **без правок `scripts/server.py`**:
  - **Свой ответ на `choice`-вопрос** — под radio-опциями всегда видимое поле `<textarea data-answer>`
    (`render()` ветка choice, `:670`/`:677`). Приходит как обычный `answer` того же `questionId`;
    свой ответ перебивает выбор опции и наоборот (`wireBlocks` `:760`/`:766`), один `answer` на вопрос
    (серверный дедуп). `answer.text` может не совпадать ни с одной `options` — это свободный ответ.
  - **Коммент к демо-варианту** — у каждого `demo.variants[]` всегда видимое поле → `comment` с
    `blockId = vr.id`, `selectedText:""` (`saveVariantComment` `:898`). `regionFooter(vr.id)` теперь
    рендерится один раз на вариант (в т.ч. без `caption`, `renderDemo` `:746`), поэтому реплаи агента
    по `blockId===vr.id` видны всегда.
  - Синхронизирована документация скилла: `skills/feature/dashboard-guide.md`,
    `skills/feature/feedback-loop.md` (контракт чтения submission для агента).
- **Зачем:** закрыть два разрыва в обратной связи (нельзя было ответить своей формулировкой; нельзя
  было прокомментировать вариант без caption — и реплаи к такому варианту были невидимы). Backend
  агностичен к содержимому items, поэтому обе фичи — чистый фронтенд + синк доков скилла.
- **Ключевое решение:** реюз существующего draft-контракта (`answer` вне `options`; `comment` с
  `blockId=vr.id`) вместо новых полей/флагов/эндпоинтов → 0 правок сервера (ADR-0008).
- **План:** `.workflow/tasks/dashboard-feedback-enhancements/plan.md`
- **ADR:** `decisions/ADR-0008-feedback-on-existing-contract-zero-server.md`
- **Область:** `areas/dashboard-feedback-ui.md`

## 2026-06-12 — new-product-workflow
- **Что:** Добавлена команда-оркестратор `/new-product` — создание продукта с нуля (greenfield).
  Стадии `INTAKE → DISCOVER → PRD → PRD-GATE → PHASE-PLAN → PLAN-GATE → BUILD → SHIP → DONE` с
  эволюционным build-loop (generate → tests → judge → refine) на каждую фазу продукта.
  - Скилл `skills/new-product/` (SKILL.md, phases.md, **loop.md**, feedback-loop.md, state-schema.md,
    dashboard-guide.md, knowledge-guide.md) — зеркало `/feature` + greenfield-стадии и спека цикла.
  - Ростер `agents/np-*.md` с пиннингом модели во frontmatter: `np-thinker` (`model: fable`, tools
    урезаны до Read/Write/Edit — структурно не читает сырьё), `np-researcher`/`np-coder`/`np-judge`
    (`model: opus`). Реюз `wf-reviewer`/`wf-documenter`.
  - Шаблоны `templates/artifacts/{prd,phase-plan,judge-verdict,iteration-scratchpad,research-digest}.md`.
  - Инфраструктура (сервер/дашборд/телеметрия) не тронута: PRD/фазы → `planBlocks`/`workstreams`,
    вердикты судьи → `reviews.json` (`kind:"judge"`); greenfield-дифф — через empty-tree `baseCommit`
    `4b825dc6…`. README, `plugin.json` (0.7.0→0.8.0), `marketplace.json`, eval-фикстура
    `evals/fixtures/greenfield-mini/`.
- **Зачем:** второй сценарий плагина — создание продукта с нуля с самоулучшающимся циклом (судья +
  тесты) при фиксированной маршрутизации моделей (fable — мыслитель на выжимках; opus —
  исследование/реализация/судейство). «Исследователь кормит мыслителя» реализовано через оркестратора
  (субагенты не спавнят субагентов).
- **Гейт-решения:** гибридный гейт (тесты — стена, судья — руль), вердикт-объект вместо pass/fail,
  заморозка PRD-производных тестов (анти-гейминг), Reflexion-scratchpad, 3 стоп-условия + эскалация,
  гейт-политика V1 (два гейта).
- **План:** `.workflow/tasks/new-product-workflow/plan.md`
- **ADR:** `decisions/ADR-0006-np-agent-roster-model-pinning.md`,
  `decisions/ADR-0007-evolutionary-build-loop.md`
- **Область:** `areas/orchestrator-skills.md`

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
