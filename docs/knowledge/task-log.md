# Журнал задач

> Append-only. Каждая задача воркфлоу оставляет запись: что и **зачем** изменено. История для будущих агентов.

<!-- Новые записи — сверху. -->

## 2026-07-05 — code-review-wizard: под-фаза REVIEW + визард ревью диффа — ADR-0027
- **Что:** новая **под-фаза REVIEW** между VERIFY и DONE у `/feature`. После зелёного VERIFY оркестратор
  берёт `git diff <baseCommit>` в worktree, **ранжирует файлы по важности** и внутри каждого — **ханки по
  важности** (комбинированная эвристика: публичный контракт/API + объём логики + риск → выше;
  переименования/формат/проброс/фикстуры → `kind:"cosmetic"`), приписывает «что/зачем» и публикует
  структуру в **новом поле `dashboard.json.review`**. Человека ведёт **новая 6-я вкладка «Ревью»** — рельс
  ранжированных файлов/блоков, чипы важности + logic/cosmetic, встроенный дифф ханка, поле комментария.
  Комментарии человека едут по **существующему каналу anchored-тредов** (якоря `rev:<path>` и
  `rev:<path>#<hunkIdx>`); агент правит код и отвечает тем же якорем; «N ждут ответа» = открытые `rev:*`;
  закрытие — сигнал `approve-plan` (кнопка «Завершить ревью», реюз plan-gate approve/`flushDraft`, ADR-0026).
- **Зачем:** вкладка «Изменения» даёт **плоский** дифф без важности/аннотаций/тред-цикла. Ревью
  собственного изменения — ранжированное, пошаговое, с «что/зачем» и петлёй комментов к агенту — раньше
  человеку было негде провести перед приземлением. Сделано **поверх контракта**, без роста сервера.
- **Как:** **0 правок `server.py`** (ride-the-contract) — `review` едет как новое агностичное поле в
  `/data`; тела ханков — из существующего `/changes?file=` (не дублируются в модель, режутся `hunkSlice`);
  комменты — `POST /chat` c verbatim `anchor`; закрытие — `approve-plan`. Якорь стабилен **по индексу
  ханка** (`rev:<path>#<idx>`), не по диапазону строк (диапазоны плывут между итерациями). FE целиком в
  `templates/dashboard.html`: вкладка `#tab-review`/`#review`, `renderReview`/`renderReviewRail`/
  `renderReviewStep`/`renderReviewStepper`/`reviewTick`/`gotoStep`/`hunkSlice`/`kindChip`; свои
  `captureReviewInput`/`restoreReviewInput` (scoped к `#review`); sig-гард с курсором шага; a11y —
  ARIA-tab + объявление шага в `#phase-announce`; 25 ключей `STR` `tab.review`/`review.*` в обоих словарях.
  Скилл: `phases.md` §6.5 REVIEW, `feedback-loop.md` «Review wizard cycle», поле `review` в
  `dashboard-guide.md`, нетерминальная фаза `REVIEW` в `state-schema.md`; `_shared/dashboard-contract.md`
  НЕ тронут (`review` feature-specific). Инвариант паритета `index.html`↔шаблон (ADR-0024) соблюдён.
- **Проверка:** `tests/test_review_wizard.py` (проза + DOM + STR). Версия плагина 0.25.0 → 0.26.0. Долг —
  пред-существующая flaky `tests/test_hub.py::HubCardCacheTest` (не связана с этой фичей).
- **План:** `.workflow/tasks/code-review-wizard/plan.md`
- **ADR:** `decisions/ADR-0027-code-review-wizard-review-subphase-ride-contract.md`; новая область
  `areas/dashboard-review-wizard.md`; обновлён feature-stage-map в `areas/orchestrator-skills.md`.

## 2026-07-01 — plan-gate «Утвердить» вбирает ответы (без принудительной доработки) — ADR-0026
- **Что:** «Утвердить план» на гейте больше **не** блокируется неотправленным черновиком. Раньше ответ на
  вопрос / выбор варианта попадал в `draft.json` и `updateApproveGate()` дизейблил approve, пока черновик
  не отправят кнопкой «Отправить агенту на доработку» — а та запускала полный revision-круг оркестратора
  (применить → реплаи → ре-парк в `awaiting-batch`). Итог для человека: `Submit → ждать ревизию → Approve`
  даже когда правок нет, только выбор вариантов.
- **Зачем:** ответы — это **входные данные** реализации, а не «доработка». Человек жаловался, что при
  отсутствии комментариев его всё равно гонят через круг ревизии и заставляют жать approve по многу раз.
- **Как:** frontend `templates/dashboard.html` — общий `flushDraft()` (авто-`POST /submit` + очистка
  черновика, снимок `dispatchPreview`), реюзают «Отправить» и «Утвердить». `/feature`: чистые выборы → один
  клик (`flushDraft`+`approve-plan`); свободный текст-правка (ответ ≠ опциям) / `comment` / открытый тред
  (`openThreadAnchors`) → инлайн-строка `#approve-ask` («Применить и в бой» / «Сначала на доработку»).
  `/improve` SELECT gate — двухкликовый армированный конфирм сохранён (первый клик авто-submit+arm). Убраны
  `submittedOnce`, степпер `①②`, нудж `submitFirst`. Оркестратор (`skills/feature/*`, `skills/improve/*`):
  на `approve-plan`+сабмишн в одном `/wait` применить ответы и идти **прямо в IMPLEMENT/DISPATCH** без
  ре-парка. **0 правок `server.py`** (ride-the-contract, lineage ADR-0008/0013/0016).
- **Проверка:** `tests/test_plan_gate_approve.py` (JS-поверхности + проза оркестратора) + полный прогон
  `unittest` (351 зелёных); e2e через playwright по фикстуре `_preview-elaborate` — проверены все три
  ветки (чистый выбор = 1 клик; открытый тред = ask-row → Apply; `/improve` = submit-then-arm-then-signal),
  `signals.json` подтвердил `submit`+`approve-plan` в одном тике. ADR-0026; INDEX + `dashboard-feedback-ui.md`
  обновлены.

## 2026-06-29 — improve-plugin DRAIN ЗАВЕРШЁН (все 19 фич приземлены на `main`; очередь 36/36)
- **Что:** **автономный** (fast-lane, без per-feature дашборда) дренаж очереди `improve-plugin` через
  `/feature` **завершён** — все **19** queued-фич реализованы, протестированы и запушены на `main`,
  **по одной за коммит** в свежем контексте (ADR-0014 последовательная очередь; ADR-0019 автономный
  дренаж). `.workflow/dispatch-queue.json` теперь **36/36 done** (17 `improve-platform-vision` + 19
  `improve-plugin`). Эта запись — **durable-журнал** прогона: в fast-lane дашборда не было, поэтому
  запись здесь и есть запись о приземлении.
- **19 фич (коммит → слуг, по темам):**
  - **Security/безопасность:** `markdown-sanitizer-controlchar-xss` (`cd9a651` — блок `javascript:`-XSS
    через control-char в URL markdown), `confined-path-guard-dedup` (`9bda480` — общий
    `_aipf.confined_path` traversal-гард, дедуп 6 копий), `csrf-origin-host-guard` (`0251bbb` —
    CSRF/DNS-rebinding-гард на state-changing POST-роутах).
  - **A11y/доступность:** `dialog-focus-trap-aria-modal` (`38385ed` — focus-trap + `aria-modal` для
    чат-панели и selection-popover).
  - **Производительность:** `hub-queue-etag` (`828221c` — conditional GET/ETag для `/hub.json` и
    `/queue.json`), `changes-tab-sig-gate` (`125ed27` — sig-гард ре-рендера вкладки «Изменения»),
    `memo-transcript-messages` (`4543d98` — мемоизация `parse_transcript_messages` по `path,mtime,size`).
  - **Надёжность:** `store-writer-durability-fsync` (`0485e2c` — fsync перед replace + lock-сериализованный
    `append_jsonl`).
  - **Тех-долг:** `dedup-server-aipf-helpers` (`81f653b` — единый источник `now_iso`/`safe_slug`/`SLUG_RE`
    + `_aipf.git`), `shared-skill-dashboard-contract` (`5ef0c73` — канонический
    `skills/_shared/dashboard-contract.md` + указатели), `tests-conftest-syspath-dedup` (`24fbdf0` —
    центральный `tests/__init__.py`-bootstrap; `discover -t .`; `dev.py`/`ci.yml` обновлены).
  - **DX:** `debug-command` (`0de7853` — новый скилл `/debug`: reproduce→root-cause→минимальный
    fix+регрессия), `makefile-devpy-check` (`a0170b9` — `Makefile` делегирует в `dev.py` + `dev.py check`).
  - **UX:** `gate-bulk-select-features` (`1e65285` — «Mark all Do»/«Clear all» на improve SELECT GATE),
    `gate-submit-approve-stepper` (`38acdff` — степпер Submit→Approve + подтверждение до диспетча),
    `improve-candidates-transparency` (`6e0e89e` — `GET /improve/candidates` + вкладка «Candidates»),
    `gate-rank-chip` (`ed602ae` — структурный `planBlocks[].rank`-чип на карточках improve-гейта).
  - **Функциональные пробелы:** `hub-queue-control-ops` (`284329a` — `queue.apply_op` + `POST /queue/op`
    + кнопки skip/retry/cancel/reorder в хабе), `hub-cross-run-cost` (`130c111` — opt-in `GET /hub/cost`
    кросс-ран roll-up стоимости).
- **Почему нет новых ADR в этой записи:** каждая фича уже несёт свой коммит и (где надо) свой ADR в
  собственном диспетченном прогоне; механика дренажа покрыта ADR-0012/0013/0014/0019. Кросс-сквозного
  решения, требующего отдельного ADR на уровне drain-записи, нет. (Существующий untracked
  `ADR-0021`-файл — из прошлого прогона, не тронут.)
- **Связь с планом:** `.workflow/tasks/improve-plugin/` (`brief.md`, `candidates.md`, `votes/aggregate.md`)
  + per-feature брифы `.workflow/tasks/<slug>/brief.md`; аудит-прогон — запись ниже.

## 2026-06-29 — improve-plugin (аудит-прогон: рой→консенсус→выбор→диспетч; 19 фич в очередь)
- **Что:** запущен `/improve` со слугом `improve-plugin` под запрос «просканировать **весь плагин** и
  найти ~20 крутых улучшений». Эта запись фиксирует **сам аудит-прогон** — прод-код здесь **не**
  меняется; правки диспетчатся как очередь `/feature` и приземляются в своих прогонах.
  - **SCOUT:** 7 read-only `wf-improver`-скаутов (по призме: UX/продукт, производительность,
    надёжность, тех-долг, DX, функциональные пробелы, доступность+безопасность) дали **47 сырых
    находок**.
  - **CONSENSUS:** оркестратор консолидировал+дедупнул до **40 кандидатов** (`candidates.md`); 3 voter'а
    `wf-improver` независимо оценили весь список; оркестратор агрегировал **детерминированно** по
    формуле ADR-0012 с **дефолтными весами `w_e=w_r=0.5`** и confidence-множителем `conf/3`
    (`votes/aggregate.md`) — поток «панель + арифметика».
  - **SELECT GATE:** показан топ-**20**. Человек выбрал **19 = «Делаем»** (пропустил `feat-10`
    «drain summary/notification»); режим дренажа = **Автономно**.
  - **DISPATCH:** дописан `.workflow/dispatch-queue.json` айтемами **n=18…36** (предыдущие 17 айтемов
    `improve-platform-vision` уже `done`; очередь `autonomous: true`, `mode: sequential-feature`,
    `baseCommit 270dacc`, `improveSlug improve-plugin`) — **19 pending** + 19 брифов под
    `.workflow/tasks/<slug>/brief.md` (ADR-0014 последовательная очередь; ADR-0019 автономный дренаж).
    `/improve` **сам** `/feature` не запускает (чистота контекста).
- **19 фич в очереди (по рангу, по темам):**
  - **Security/безопасность:** `markdown-sanitizer-controlchar-xss` (1), `confined-path-guard-dedup` (3),
    `csrf-origin-host-guard` (4).
  - **A11y/доступность:** `dialog-focus-trap-aria-modal` (19).
  - **Производительность:** `hub-queue-etag` (7), `changes-tab-sig-gate` (14),
    `memo-transcript-messages` (15).
  - **Надёжность:** `store-writer-durability-fsync` (6).
  - **Тех-долг:** `dedup-server-aipf-helpers` (8), `shared-skill-dashboard-contract` (11),
    `tests-conftest-syspath-dedup` (13).
  - **DX:** `debug-command` (9), `makefile-devpy-check` (16).
  - **UX:** `gate-bulk-select-features` (2), `gate-submit-approve-stepper` (5),
    `improve-candidates-transparency` (12), `gate-rank-chip` (17).
  - **Функциональные пробелы:** `hub-queue-control-ops` (10), `hub-cross-run-cost` (18).
- **Почему здесь нет нового ADR:** механика рой/консенсус/выбор/очередь/автономный-дренаж уже покрыта
  ADR-0012/0013/0014/0019 — прогон переиспользует их без изменений (на сей раз **дефолтные веса**
  `w_e=w_r=0.5`, без brief-knob прошлого прогона). Технические ADR для queued-но-непостроенных фич
  (markdown-санитайзер, CSRF-гард, `/debug`, fsync-durability, …) пишут **те** диспетченные прогоны
  `/feature`, когда реализуют их — не эта запись. Существующий untracked `ADR-0021`-файл не тронут.
- **Объём этого прогона:** read-only аудит + запись очереди + эта запись в БЗ. **Прод-код здесь не
  менялся.** Артефакты: `.workflow/tasks/improve-plugin/` (`brief.md`, `candidates.md`,
  `votes/aggregate.md`).

## 2026-06-28 — release: бамп плагина 0.23.1 → 0.24.0 (батч improve-platform-vision)
- **Что:** `.claude-plugin/plugin.json` version 0.23.1 → **0.24.0**; описания в `plugin.json` и
  `marketplace.json` обновлены под новую поверхность (вкладка «Артефакты» + команды `/design`, `/test`,
  `/docs`, `/start`). `marketplace.json.metadata.version` (1.0.0) — версия маркетплейса, не трогаем.
- **Зачем:** минорный бамп под 17-фичевый батч improve-platform-vision (3 новые команды + вкладка
  Artifacts + визуализация процесса + надёжность/перф/безопасность).

## 2026-06-28 — CI fix: Windows tempfile cleanup flake (test_queue / test_dispatch_queue)
- **Что:** 16 голых `tempfile.TemporaryDirectory()` в `tests/test_queue.py` и
  `tests/test_dispatch_queue.py` → `tempfile.TemporaryDirectory(ignore_cleanup_errors=True)`.
- **Зачем:** CI начала падать в дренаже improve-platform-vision — но **только Windows-ячейки**
  (`windows-latest`, любой Python 3.11/3.12/3.13, случайно 1–2 из 3), при зелёном ubuntu/macos и
  локальном Windows (8/8). Диагноз через GitHub Actions API (логи admin-only): классический
  Windows-флейк — голый `TemporaryDirectory()` **кидает** на очистке, если файл ещё держит хэндл/
  антивирус/индексатор. Нарушало `conventions.md` (mkdtemp + ignore_errors). `ignore_cleanup_errors`
  доступен с Python 3.10 (вся CI-матрица 3.11+).
- **Триггер:** флейк начался на feat-14 (накопление тестов с `TemporaryDirectory()` повысило шанс
  поймать очистку под хэндлом); feat-1's `test_queue` проскакивал «удачно».
- **Доки:** `conventions.md` дополнен предупреждением про этот Windows-флейк.
- **Файлы:** `tests/test_queue.py`, `tests/test_dispatch_queue.py`, `docs/knowledge/conventions.md`.
  Тесты: 298 зелёных локально; `check_stdlib` чист.

## 2026-06-28 — artifacts-versioning-diff (версии артефактов + дифф между версиями) — ПОСЛЕДНЯЯ ФИЧА ОЧЕРЕДИ
- **Что:** вкладка «Артефакты» (feat-17) получила **селектор версий + inline-дифф**. Фича feat-20/
  cand-5, на `main`. Завершает очередь improve-platform-vision (17/17).
  - **Фронт:** в группе с >1 версией — чипы версий `v3 v2 v1` (`art-vchip`, выбранная подсвечена); выбор
    версии меняет, какую превьюить/скачивать/диффать. Для текстовых артефактов (doc/diff/svg/html) —
    кнопка **«Diff vs prev»**: клиентский LCS-line-дифф (`lineDiff`, бакет O(n·m)≤4M) выбранной версии
    против предыдущей, рендер с +/- (своя `.art-diff`-разметка, визуальный язык вкладки «Изменения»).
    Тексты тянутся через существующий `/artifact` (`artText`); 0 новых эндпоинтов.
  - **Конвенция:** `<name>.v<N>.<ext>` (уже под `ARTIFACT_RE`/`MOCKUP_RE`, парсит `_artifact_base_version`
    из feat-17). Оркестраторы при перегенерации инкрементят `N`, не затирают. Задокументировано в
    `skills/improve/dashboard-guide.md` (+ ADR-0025).
- **Зачем:** перерисовка артефакта (`/ask` infographic/process, `/design` redesign) затирала прошлую
  версию — «было/стало» терялось.
- **Ловушка (повтор feat-17):** диф-контейнер не должен наследовать grid `.diff .dl` вкладки «Изменения»
  (3 колонки с `.ln`) — свой класс `.art-diff` + своя `.dl`-разметка (2 ячейки `.sg`/`.code`).
- **Проверено в браузере (preview):** чипы v2*/v1, «Diff vs prev» дал корректный line-дифф
  (`-Step 2: draft` / `+Step 2: draft the API` / `+Step 3: review`), заголовок `plan.v1.md → plan.v2.md`.
- **Файлы:** `templates/dashboard.html`, `skills/improve/dashboard-guide.md`. Тесты: 298 зелёных,
  `check_stdlib` чист. i18n `artifacts.diff*` в обоих STR.

## 2026-06-28 — docs-command (команда /docs — генерация/рефреш доков + аудит дрейфа)
- **Что:** добавлена команда **`/docs`** — только-документация для области + аудит дрейфа док↔код. Фича
  feat-19/cand-15, на `main`. Новый `skills/docs/`: `SKILL.md` (name: docs, description разведён от
  ask/feature/improve) + `phases.md` (`INTAKE→AUDIT→COMPOSE→PLAN GATE→WRITE→DONE`) + общие reference-файлы
  (из `skills/feature/`).
- **Машина:** AUDIT (`ask-researcher` read-only сверяет area-доки/README/ADR/`INDEX.md` с **кодом** →
  drift-список `{doc path:line, claim, code path:line, reality, fix}`) → COMPOSE правок → гейт плана
  (дифф доков, вкладка «Изменения») → WRITE (`wf-documenter` пишет + рефрешит `INDEX.md`). **Реюзит
  ростер** (ask-researcher + wf-documenter) — нового агента нет.
- **Инварианты:** **не меняет поведение** кода (нашёл код-баг → не чинит, это `/feature`); docs/README —
  **eng-first**; регистрация конвенцией каталога.
- **Тест:** `tests/test_docs_command.py` (3 кейса) — frontmatter `name: docs`+description, reference-файлы,
  фазы машины.
- **Файлы:** `skills/docs/*` (новый каталог), `tests/test_docs_command.py` (новый),
  `docs/knowledge/areas/orchestrator-skills.md` (7-я полноценная команда). Тесты: 298 зелёных (3 новых),
  `check_stdlib` чист.

## 2026-06-28 — queue.py UTF-8 stdout fix (мелкий, найден в дренаже)
- **Что:** `queue.py` форсит UTF-8 на `stdout`/`stderr` (errors=replace) на старте `main()`. Заголовки
  айтемов с non-ASCII (кириллица «доки↔код») роняли `queue.py next` на Windows-консоли (legacy codepage,
  `UnicodeEncodeError`). Реконфиг с гардом под старые Python. Найдено при дренаже feat-19.

## 2026-06-28 — trace-incremental-rebuild (мемоизация парсинга транскриптов /trace)
- **Что:** убрано главное узкое место вкладки «Трейсинг». Фича feat-18/cand-33, на `main`.
  - **`parse_transcript_usage` мемоизирована** по `(path, mtime, size)` (`_aipf._USAGE_CACHE`, бакет 512
    с простым clear): неизменённый транскрипт НЕ перечитывается на каждый поллинг `/trace` — главная
    стоимость `build_trace` (мегабайтные транскрипты). Возвращается **shallow-копия** (`dict(result)`),
    чтобы `build_trace` (дописывает `_fe`) не портил кэш.
  - **Кэш `_trace` сервера** ключуется сигнатурой `(mtime, size)` telemetry.jsonl (было только mtime) +
    TTL 3с — даёт периодический рефреш токенов растущих транскриптов, теперь **дешёвый** (перечитываются
    только изменённые транскрипты).
- **Зачем:** `build_trace` на каждый промах 3с-кэша перечитывал весь telemetry И заново парсил КАЖДЫЙ
  транскрипт (telemetry-tracing.md сам называл это узким местом); богаче live-визуализация только
  усиливала стоимость.
- **Тест:** `tests/test_trace_cache.py` (3 кейса): неизменённый файл кэшируется (равный результат);
  возвращаемая запись — копия (мутация `_fe` caller'ом не портит кэш); дописанный файл (новый size)
  перепарсивается (msgs растёт). Существующие trace-тесты зелёные (build_trace не сломан).
- **Файлы:** `scripts/_aipf.py`, `scripts/server.py`, `tests/test_trace_cache.py` (новый),
  `docs/knowledge/areas/telemetry-tracing.md`. Тесты: 295 зелёных (3 новых), `check_stdlib` чист.

## 2026-06-28 — artifacts-panel-tab (вкладка «Артефакты» — аналог Claude Artifacts) ⭐
- **Что:** добавлена **5-я вкладка «Артефакты»** — браузируемые осязаемые выходы агента. Флагман
  запроса (аналог Claude Artifacts). Фича feat-17/cand-4, вбирает cand-6 (security-армор), на `main`.
  Новый ADR-0025.
  - **Сервер:** `GET /artifacts?slug=` — листинг-метаданные файлов из `<task>/mockups/` + `<task>/
    artifacts/` (allowlist `ARTIFACT_RE`, `{name,dir,kind,active,size,mtime,base,version}`,
    `_artifact_base_version` парсит `<name>.v<N>.<ext>`). `GET /artifact?file=[&download=1]` — confined-
    serve как `_serve_mockup` (realpath+commonpath по обоим dir).
  - **Инвариант безопасности (cand-6):** ACTIVE-контент (html/svg) отдаётся с `MOCKUP_SEC_HEADERS`
    (sandbox-CSP + nosniff) и рендерится ТОЛЬКО в `sandbox="allow-scripts"`-iframe без `allow-same-
    origin` + `referrerpolicy=no-referrer`, **никогда innerHTML**; inert (md/json/img) — nosniff;
    `download=1` → `Content-Disposition: attachment`. Реюз `MOCKUP_CSP` — одна модель безопасности.
  - **Фронт:** вкладка + галерея (`renderArtifacts`/`artifactsTick`, sig-гард, гейт `document.hidden`),
    группировка по `base` (новые версии первыми), per-card Preview (iframe/`<img>`) + Download. i18n
    `tab.artifacts`/`artifacts.*` в обоих STR.
- **`<task>/artifacts/`** — новый контракт для не-мокап-выходов; версии feat-20 углубит.
- **Тест:** `tests/test_artifacts.py` (7 кейсов): листинг по обоим dir + фильтр allowlist; active→CSP+
  nosniff; inert→nosniff без CSP; download→attachment; traversal/bad-name→404; base/version-хелпер.
  Проверено в браузере (preview): вкладка листает 2 артефакта, sandbox-iframe рендерит agent-HTML
  (inline-скрипт исполнился В sandbox), security-заголовки live (CSP+nosniff / attachment / 404).
- **Ловушка (зафиксировать):** `esc()` падает на не-строке — `esc(a.version)` (число) кидал
  `(s||"").replace is not a function`; версия — int, esc не нужен (`v${a.version|0}`).
- **Файлы:** `scripts/server.py`, `templates/dashboard.html`, `tests/test_artifacts.py` (новый),
  `docs/knowledge/decisions/ADR-0025-...md` (новый), `INDEX.md`. Тесты: 292 зелёных (7 новых),
  `check_stdlib` чист.

## 2026-06-28 — review-gate-atomic-writes (атомарная запись reviews.json + stale-running)
- **Что:** зафиксирован контракт записи `reviews.json` (журнал VERIFY-гейтов). Фича feat-16/cand-25,
  на `main`. Контракт-фича (кода нет — `_aipf.write_json` уже атомарен; предписываем его использовать).
  - **phases.md §VERIFY:** запись `reviews.json` **атомарно** (`_aipf.write_json`→`atomic_write`,
    ADR-0021), не сырым truncate — под автономным `/loop /feature` это read-modify-write в общий store
    по сессиям; полузапись убила бы единственный журнал гейтов. Каждый run несёт `startedAt` при
    `running`. **Resume-инвариант VERIFY:** stale `running` (есть startedAt, нет терминала, не в полёте)
    → трактуется как `failed` и гейт **перезапускается**; не дойти до DONE с висящим `running`/
    high-severity. Зеркало stale-`in-progress` recovery очереди (feat-14).
  - **feedback-loop.md:** в shape `reviews.json` добавлен `startedAt`; нота про атомарную запись +
    stale-running.
- **Зачем:** review-гейт — единственная страховка качества автономного дренажа (ADR-0019); его журнал
  не должен застревать в `running` после краша или биться при полузаписи.
- **Тест:** `tests/test_review_gate_contract.py` (2 кейса) — phases.md предписывает atomic+stale+re-run;
  feedback-loop shape несёт startedAt + atomic-ноту (контракт-проза не дрейфует).
- **Файлы:** `skills/feature/phases.md`, `skills/feature/feedback-loop.md`,
  `tests/test_review_gate_contract.py` (новый). Тесты: 285 зелёных (2 новых), `check_stdlib` чист.

## 2026-06-28 — start-intent-router (лёгкий роутер /start)
- **Что:** добавлен **`/start`** — конверсационный роутер намерения. Фича feat-15/cand-19, на `main`.
  **Единственный файл** `skills/start/SKILL.md` (frontmatter `name: start`, без reference-бандла — роутер
  не гоняет машину/дашборд). Человек описывает задачу словами → классификация против `description`-полей
  установленных команд → **рекомендация** (дефолт) или **делегирование** одним Skill-хэндоффом с
  прокидыванием языка запроса.
- **Таблица маршрутизации:** Q&A→`/ask`, фича→`/feature`, greenfield→`/new-product`, аудит→`/improve`,
  UI компонента→`/design`, тесты→`/test`, баг→`/feature` (debug может появиться позже), ревью диффа→
  `/code-review` (review может появиться позже). Правило **«route, not execute»** — сам ничего не
  исследует/правит (минимальный риск); 0 новой инфраструктуры.
- **Тест:** `tests/test_start_router.py` (3 кейса) — frontmatter `name: start`+description, роутер
  называет все 6 реальных команд (не дрейфует от реестра), self-contained.
- **Файлы:** `skills/start/SKILL.md` (новый), `tests/test_start_router.py` (новый),
  `docs/knowledge/areas/orchestrator-skills.md` (седьмая команда — роутер). Тесты: 283 зелёных (3 новых),
  `check_stdlib` чист.

## 2026-06-28 — drain-recovery-stale-failed (восстановление дренажа: stale in-progress + failed)
- **Что:** у дренажа очереди появились **определённые** пути восстановления/отказа. Фича feat-14/
  cand-23, на `main`. Краш-сессия больше не теряет фичу молча.
  - **Код (`queue.py`):** `next` теперь **шаг 0 — self-heal**: любой `in-progress` со `startedAt` старше
    `--max-running-age` (дефолт 1800с) возвращается в `pending` (`resumedFrom:"in-progress"`, `startedAt`
    очищен) и переподбирается. Дренаж последовательный → залипший `in-progress` = мёртвая сессия, безопасно
    резюмить. Standalone `queue.py recover [--age N]`. Хелперы `_age_secs`/`_recover_stale`.
  - **Контракт (доки):** `dispatch-queue.md` §«Recovery & failure» — оба пути: stale→pending (self-heal)
    и unreachable-DONE→`failed`+`failReason` с эскалацией (anchored `chat.jsonl` `needsAnswer` +
    `state.questions[]`, реюз hard-block ADR-0019), затем продолжить со следующего; `dispatched[]` —
    снимок-на-DISPATCH (очередь канонична). `feature/SKILL.md` queue-mode шаг 0 зовёт `queue.py next`
    (авто-recovery) и определяет failure-путь.
  - **Счётчик failed в хабе УЖЕ был** (`renderQueue` считает `failed` → `failMeta` «· N сбоев»,
    `hubq.failure*`) — критерий выполнен существующим кодом; добавлять не пришлось.
- **Тест:** `tests/test_dispatch_queue.py` `StaleRecoveryTest` (3 кейса): stale→pending но recent не
  трогаем; `next` без pending восстанавливает залипший и переподбирает; свежий `in-progress` переживает
  `next`.
- **Файлы:** `scripts/queue.py`, `tests/test_dispatch_queue.py`, `skills/improve/dispatch-queue.md`,
  `skills/feature/SKILL.md`. Тесты: 280 зелёных (3 новых), `check_stdlib` чист.

## 2026-06-28 — test-command (шестая команда /test — генерация тестов)
- **Что:** добавлена команда **`/test`** — только-тесты для существующего модуля/области. Фича
  feat-13/cand-13, на `main`. Новый каталог `skills/test/`: `SKILL.md` (frontmatter `name: test`,
  description разведён от feature/review/improve/ask) + `phases.md` (машина
  `INTAKE→ANALYZE→PLAN→PLAN GATE→IMPLEMENT→VERIFY→DONE`) + общие reference-файлы (dashboard-guide,
  feedback-loop, state-schema, knowledge-guide, parallel — скопированы из `skills/feature/`).
- **Машина:** ANALYZE (`wf-explorer` read-only находит непокрытые ветви/контракты → gap-list) → PLAN
  (`wf-planner` → план тестов) → гейт плана → IMPLEMENT (`wf-coder` пишет `tests/test_*.py` по
  `conventions.md` §tests) → VERIFY (**зелёный прогон = гейт**, `wf-reviewer` бракует тавтологии).
  **Реюзит ростер `wf-*`** — нового агента нет.
- **Инварианты:** **не меняет поведение** кода (нашёл баг → не чинит, это `/feature`); тесты по
  конвенции (оффлайн unittest, sys.path-хак, tempfile, кросс-платформенность); регистрация **конвенцией
  каталога** (`plugin.json` не трогаем — это работа `/release`).
- **Тест:** `tests/test_test_command.py` (3 кейса) — frontmatter `name: test`+description, наличие
  reference-файлов, фазы машины в phases.md.
- **Файлы:** `skills/test/*` (новый каталог), `tests/test_test_command.py` (новый),
  `docs/knowledge/areas/orchestrator-skills.md` (шестая команда). Тесты: 277 зелёных (3 новых),
  `check_stdlib` чист. (Эффект — после refresh плагина появится команда `/test`.)

## 2026-06-28 — hub-live-now-command-center (живой «Сейчас» по всем задачам в хабе)
- **Что:** `/hub` из пассивного списка стал командным центром. Фича feat-12/cand-8, на `main`.
  - **Сервер:** `_hub_build_card` добавил в карточку `now`/`nowAt` из `dashboard.json` (уже в
    `_hub_signature` → мемо инвалидируется при смене `now`). Контракт-тест ключей карточки
    (`test_hub.CARD_KEYS`) обновлён.
  - **Фронт (HUB_PAGE):** `heroCard` рисует строку **«Сейчас: <now> · <age>»** (`nowLine`/`fmtAge`):
    скрыта при `awaiting`/пустом `now`, грейснута при `nowAt` старше 90с (как `renderNow` на дашборде).
    В шапке «Активные» — счётчик **«N ждут вас»** (`pill.awaiting`) по полю `awaiting`.
  - i18n: `hub.now`/`hub.awaitingYou` в обоих HUB_PAGE STR (`en`/`ru`).
- **Зачем:** при параллельных прогонах человек кликал в каждую карточку, чтобы понять, кто что делает /
  кто его ждёт. Теперь видно сразу.
- **0 новых эндпоинтов** — `now` едет в существующем `/hub.json`. mtime-кэш карточек цел (now в
  сигнатуре). Проверено в браузере (fresh HUB_PAGE): fresh→строка с age, stale>90с→грейс без age,
  awaiting/пусто→скрыто, heroCard несёт строку.
- **Файлы:** `scripts/server.py`, `tests/test_hub.py` (CARD_KEYS), `docs/knowledge/areas/parallel-runs-hub.md`.
  Тесты: 274 зелёных, `check_stdlib` чист.

## 2026-06-28 — server-security-headers (frame-ancestors + Referrer-Policy)
- **Что:** companion-сервер шлёт заголовки безопасности. Фича feat-11/cand-45, на `main`.
  - **`_send` (один общий путь ответа):** `Referrer-Policy: no-referrer` на **каждый** ответ (slug/путь
    не утекают во внешний Referer); для `text/html` — `X-Frame-Options: SAMEORIGIN` +
    `Content-Security-Policy: frame-ancestors 'self'` (анти-кликджекинг дашборда/хаба). Если вызыватель
    уже задал CSP (sandbox-демо), он сохраняется — `_send` не перетирает.
  - **`MOCKUP_CSP`** дополнен `frame-ancestors 'self'` — мокап встраивается только same-origin.
  - **Дашборд:** внешние `target=_blank` ссылки → `rel="noopener noreferrer"`; demo-iframe →
    `referrerpolicy="no-referrer"` (defence-in-depth поверх глобального заголовка; хаб покрыт самим
    заголовком).
- **Зачем:** дашборд/хаб отдавались без frame-ancestors (кликджекинг по submit/approve) и без
  Referrer-Policy (утечка локального slug/пути).
- **Тонкость:** добавляется ТОЛЬКО `frame-ancestors` (не `default-src`/`script-src`) — инлайновые
  скрипты дашборда не ломаются (CSP ограничивает лишь фрейминг). sandbox-демо работает как прежде.
- **Тест:** `tests/test_security_headers.py` (4 кейса) — HTML несёт frame+referrer; JSON только
  referrer (без frame); CSP вызывателя сохранён; `MOCKUP_CSP` ограничивает frame-ancestors.
- **Файлы:** `scripts/server.py`, `templates/dashboard.html`, `tests/test_security_headers.py` (новый).
  Тесты: 274 зелёных (4 новых), `check_stdlib` чист.

## 2026-06-28 — dashboard-polling-efficiency (гейт скрытой вкладки + settings ETag + инкрем. чат)
- **Что:** снижен холостой ход поллинга дашборда. Фича feat-10/cand-37, на `main`. Три части:
  - **Гейт скрытой вкладки:** `tick` и `syncLang` теперь `if(document.hidden) return` (как уже было у
    `chatTick`/`feedTick`/trace/changes); догон на `visibilitychange` дозван `tick()`+`syncLang()`.
  - **Фоновый awaiting-сигнал сохранён (важно!):** просто гейтить `tick` нельзя — он несёт фоновое
    браузер-уведомление «ваш ход» (срабатывает именно когда вкладка скрыта). Логика вынесена в
    `notifyAwaiting(data)` (владеет `prevStatus`), а новый **медленный `awaitingWatch`** (10с) опрашивает
    `/data` пока вкладка скрыта и объявляет переход working→awaiting. Так `tick` полностью гейчен, а
    уведомление+title-бейдж живут. render() зовёт тот же `notifyAwaiting` — переход детектится один раз.
  - **`/settings.json` ETag:** value-based `W/"lang-<lang>"` + 304 (был «no cache», опрос каждые 3с).
  - **Инкрементальный чат:** `/chat?since=<offset>` отдаёт только новые строки (реюз
    `_aipf._iter_lines_from`, как `/trace/feed`) + `nextOffset`; `since=0` — полный (back-compat, ETag).
    Клиент `chatTick` ведёт `lastChatOffset`, **дозаписывает** хвост (renderChat уже умеет append).
    **Version-skew-гард:** если сервер не вернул `nextOffset` (старый), клиент откатывается на
    full-replace — без дублей.
- **Зачем:** `tick`/`syncLang` тикали даже на скрытой вкладке; `/settings.json` без кэша; `/chat` на
  изменённом тике перепарсивал весь файл.
- **Проверено:** браузер (новый клиент+сервер) — `lastChatOffset` дополз до 570, `chatMsgs=2` без
  дублей; юнит-тест `ChatIncrementalTest` (since-хвост: полный→пустой→только новое).
- **Файлы:** `scripts/server.py` (chat since, settings ETag), `templates/dashboard.html`,
  `tests/test_conditional_get.py` (новый тест). Тесты: 270 зелёных (1 новый), `check_stdlib` чист.

## 2026-06-28 — gate-dispatch-preview (превью набора фич к диспетчу на SELECT GATE)
- **Что:** на SELECT GATE `/improve` после Submit и до Approve в actionbar показывается панель
  `#dispatch-preview` — ранговый список «уйдут в диспетч фичи: N» с title'ами. Фича feat-9/cand-10,
  на `main`, чистый FE.
- **Зачем:** человек жал Submit→Approve вслепую к итоговому набору (был только чип «N отмечено»).
- **Ключевой момент (контракт):** после Submit клиент чистит `draftItems`, а `draft.json` не в
  `READABLE_FILES` — значит выбор после Submit **не прочитать с сервера**. Решение: снимок выбора
  делается на КЛИЕНТЕ в момент Submit (`markedFeatures()` → `dispatchPreview`, ДО очистки draft) и
  показывается до Approve. Прячется, как только человек снова правит (`draftItems` непуст → снимок
  устарел), и очищается на Approve. Контракт Submit→Approve и POST-флоу не тронуты.
- **Реюз:** Do-детект из `updateDispatchChip` (первый option ИЛИ свободный текст `^делаем|делай|do`);
  порядок — по `planBlocks` (ранговый feat-1…feat-K). i18n `dispatch.preview.title` в `en`/`ru`.
- **0 правок сервера.** Проверено в браузере: feat-1+feat-3 (вкл. свободное «делаем, но без X»),
  feat-2 «Пропускаем» исключён; прячется при правке; очищается на Approve.
- **Файлы:** `templates/dashboard.html`. Тесты: 269 зелёных, `check_stdlib` чист.

## 2026-06-28 — dispatch-queue-schema-validation (контракт-тест очереди + карантин битой)
- **Что:** схема `.workflow/dispatch-queue.json` зафиксирована исполнимой спекой. Фича feat-8/cand-22,
  на `main`. Строит поверх `queue.validate`/`load_queue` из feat-1 (dispatch-queue-cli):
  - **Ужесточён `queue.validate`:** `TOP_REQUIRED = version/source/mode/baseCommit/items` (было только
    `version`); плюс прежние per-item ключи, enum статусов, плотный 1-based `n`, дубль slug. `cmd_append`
    теперь сеет `baseCommit: None`, чтобы созданная им очередь проходила контракт.
  - **Карантин битой очереди:** `_quarantine_corrupt` переносит непарсимый файл в
    `dispatch-queue.json.corrupt-<TS>` (без `:` — NTFS) + громкий warning, `_load_or_die` зовёт его на
    `corrupt`/`malformed` и падает exit 1. Битая очередь больше НЕ выглядит как слитая. Зеркало
    `state-json-corrupt-recovery` (`worktree.py`).
  - **`tests/test_dispatch_queue.py`** (новый, 9 кейсов): контракт через `queue.validate` (все статусы
    валидны; каждый пропущенный top-level/per-item ключ ловится; enum; дыра/порядок `n`; дубль slug) +
    карантин (corrupt→exit 1 + файл уехал в `.corrupt-*`; missing→exit 3, не карантин). Валит CI при дрейфе.
- **Зачем:** очередь — durable-носитель дренажа, но её схема нигде не проверялась; `_queue` (server.py)
  сливал missing/empty/corrupt в `{"items":[]}` — провал был невидим.
- **Файлы:** `scripts/queue.py`, `tests/test_dispatch_queue.py` (новый), `tests/test_queue.py` (правка
  валид-теста под полный top-level), `skills/improve/dispatch-queue.md`. Тесты: 269 зелёных (9 новых),
  `check_stdlib` чист; живая очередь дренажа валидна (17 items).

## 2026-06-28 — improve-swarm-vote-viz (визуализация роя и голосования /improve)
- **Что:** рой 7 скаутов и панель 3 голосующих в `/improve` теперь видны на дашборде. Фича feat-4/
  cand-3, на `main`. Две части:
  - **Дашборд:** карточка «Work-streams» в `#content` теперь рендерит ячейки сеткой (`.ws-grid`,
    `auto-fill minmax(200px,1fr)`) — рой призм/голосующих тайлится в сетку running→done. Тот же
    `{title,status}`-путь; `renderWsTrack` (сегментный трек) и `renderWsSummary` (чип счётчика) в
    сайдбаре не тронуты, реюз без дублирования.
  - **Скилл:** `skills/improve/phases.md` — на SCOUT оркестратор сеет `dashboard.json.workstreams[]`
    = одна запись на призму (`in_progress`→`done` по мере возврата скаута) + `now` «рой сканирует
    призмы (N/7)»; на CONSENSUS — 3 голосующих как work-stream'ы + `now` с числом кандидатов.
    `dashboard-guide.md` дополнен контрактом (workstreams больше НЕ «unused by /improve»).
- **Зачем:** самое заметное в `/improve` (7 призм, 3 голоса, агрегация ADR-0012) было невидимо —
  человек не видел ни «7 призм исследуют», ни «голоса собраны».
- **0 правок сервера** — через существующие `workstreams[]`/`now`. Проверено в браузере: SCOUT-сценарий
  даёт сетку из 7 призм-карточек со статусами + 7 сегментов в сайдбар-треке.
- **Файлы:** `templates/dashboard.html`, `skills/improve/phases.md`, `skills/improve/dashboard-guide.md`.
  Тесты: 260 зелёных, `check_stdlib` чист. (Эффект — для будущих `/improve`-прогонов после refresh плагина.)

## 2026-06-28 — workflow-now-pulse (живой пульс «Сейчас» на Workflow-вкладке)
- **Что:** компактный live-пульс активности в сайдбаре (`#now-pulse`/`renderPulse`): до 4 строк —
  running-спаны первыми, затем самые свежие действия (лейн · инструмент · аргумент), с живой точкой
  на running. Виден на ЛЮБОЙ вкладке (в т.ч. Workflow, где человек ждёт гейта), а не только в
  «Трейсинге». Фича feat-3/cand-2, на `main`.
- **Зачем:** единственный live-канал (лента действий) был заперт во вкладке «Трейсинг» и тикал лишь
  при `activeTab==='trace'`. Ждущий у гейта видел только статичную строку «Сейчас» (протухает 90с).
- **Архитектура (важно для будущих правок):** `feedModel`/`lastFeedOffset` — **один потребитель**
  since-оффсета `/trace/feed` (иначе два поллера гонкой делят события пополам). Поэтому `traceTick`
  **расщеплён**: `traceTick` теперь тянет только тяжёлый `/trace` (рендер карточек, лишь при открытой
  вкладке «Трейсинг»), а новый **always-on `feedTick`** владеет лёгким `/trace/feed`, гонит пульс на
  каждой вкладке и полный фид при активной «Трейсинг». Оба гейтятся `document.hidden`. `feedTick`
  заведён в `init` (`setInterval 4000`), в catch-up на `visibilitychange` и в `switchTab` (мгновенный
  рендер фида из общей модели). Реюз `feedLanes()`/`actType`/`actName` — без дублирования лейн-логики.
- **i18n:** `pulse.title`/`pulse.running` в обоих STR (`en`/`ru`). **0 правок сервера** —
  `/trace/feed` уже был. Проверено в браузере: «Live activity · 3 running» с running-точками.
- **Файлы:** `templates/dashboard.html`. Тесты: 260 зелёных, `check_stdlib` чист.

## 2026-06-28 — workflow-phase-rail (живой граф фаз воркфлоу + анонс смены фазы)
- **Что:** в `templates/dashboard.html` добавлен степпер фаз в сайдбаре (`renderPhaseRail`): узлы
  фаз done/active/upcoming с подсветкой текущей, плюс sr-only `aria-live`-диктор `#phase-announce`,
  объявляющий ТОЛЬКО дельту при фактической смене фазы. Фича feat-2/cand-1, приземлена на `main`.
- **Зачем:** машина стадий нигде не показывалась как конвейер — только бейдж `#phase`. Теперь видно,
  где прогон сейчас, что пройдено и что впереди; смена фазы доступна скринридеру.
- **Как:** последовательности фаз захардкожены на вид прогона (`PHASE_SEQUENCES`: feature / improve /
  design). `kind` (`dashboard.json`/`state.json`) авторитетен, если есть; иначе последовательность
  **выводится** из текущей фазы (активные фазы feature/improve уникальны → вывод точен). i18n: подписи
  фаз добавлены в оба STR-словаря (`en`/`ru`); парность ловит `tests/test_settings.py`. **0 правок
  сервера** — чистый FE поверх существующих полей.
- **Ловушка (зафиксировать):** имя `phaseLabel` уже занято объектным лейблером таймлайна «Трейсинг»
  (`m.lkey`/`m.label`) ниже по файлу — function-declaration позже перетирает раньше. Новый лейблер
  назван `railLabel`, чтобы не коллизить. Проверено в браузере (preview-сервер): рейл рендерит
  `Explore·Elaborate·Plan gate (done) → Implement (active) → Verify·Done (upcoming)`, диктор — «Phase:
  Implement».
- **Файлы:** `templates/dashboard.html`. Тесты: 260 зелёных, `check_stdlib` чист.

## 2026-06-28 — dispatch-queue-cli (queue.py: атомарный CLI очереди дренажа)
- **Что:** добавлен `scripts/queue.py` — stdlib-CLI для `.workflow/dispatch-queue.json` с подкомандами
  `next` / `done` / `fail` / `skip` / `status` / `append` / `validate`; обёртка `python dev.py queue`;
  тест `tests/test_queue.py` (14 кейсов). Первая фича дренажа очереди improve-platform-vision (feat-1/
  cand-21), приземлена **последовательно на `main`**.
- **Зачем:** очередь — единственный durable-носитель состояния дренажа, и до сих пор её статусы правил
  **только промпт-агент** ручной правкой JSON, без атомарной записи (а файл в общем store под
  конкуренцией, ADR-0010/0021). Полузапись читалась назад как `{"items": []}`, и дренаж молча считал
  очередь пустой. CLI снимает это: все мутации идут через `_aipf.atomic_write` (ADR-0021), как
  `worktree.py` сняло worktree-танец.
- **Ключевой инвариант:** `queue.load_queue(path) → (data, status)` **различает `missing` (нет очереди)
  и `corrupt` (есть, но не парсится)** — битая очередь сообщается громко (exit 1), не выдаётся за
  слитую. `queue.validate(obj) → [errors]` (top-level/per-item ключи, enum статусов, плотный 1-based
  `n`, дубль slug) — импортируется тестом и будущей фичей контракт-теста (`dispatch-queue-schema-
  validation`, feat-8). `next` печатает поля `KEY=VALUE` и exit 3, когда pending нет.
- **Контракт:** `skills/improve/dispatch-queue.md` дополнен секцией «Mutate via the CLI, never by hand»
  — скиллы зовут CLI вместо ручной JSON-правки.
- **Файлы:** `scripts/queue.py` (новый), `tests/test_queue.py` (новый), `dev.py` (подкоманда `queue`),
  `skills/improve/dispatch-queue.md`. Тесты: 260 зелёных (14 новых), `check_stdlib` чист.

## 2026-06-28 — improve-platform-vision (аудит-прогон: рой→консенсус→выбор→диспетч; 17 фич в очередь)
- **Что:** запущен `/improve` со слугом `improve-platform-vision` под запрос человека «развитие
  платформы» — новые скиллы/команды, заметная визуализация процесса на дашборде, аналог
  Claude-Artifacts и починка существующих скиллов. Эта запись фиксирует **сам аудит-прогон**; правки
  здесь **не** реализуются — они диспетчатся как очередь `/feature` и приземляются в своих прогонах.
  - **SCOUT:** 7 read-only `wf-improver`-скаутов (по призме: UX/product, performance, reliability,
    tech-debt, DX, functionality gaps, accessibility+security) дали **60 сырых кандидатов**.
  - **CONSENSUS:** оркестратор консолидировал+дедупнул до **47 кандидатов** (`cand-1…cand-47`); 3
    voter'а `wf-improver` независимо оценили весь список; оркестратор агрегировал **детерминированно**
    по формуле ADR-0012 (`score=(mean(imp)−w_e·mean(eff)−w_r·mean(rsk))·mean(conf)/3`), drop `keep==0`
    (`cand-18` `/migrate`, `cand-42` STR→data) — поток «панель + арифметика».
  - **Brief-driven knob (`w_e=0.3`, `w_r=0.5`):** вес усилия **понижен** с дефолтного `0.5` до `0.3`,
    потому что человек явно попросил **амбицию над полировкой** — чтобы высоко-импактные, но затратные
    продуктовые ставки (прежде всего вкладка «Артефакты», `cand-4`, eff=3) не выбивались из контенции
    штрафом за усилие. Это **прецедент**: эффективностный вес — это **управляемая брифом ручка**
    агрегации (не константа). См. примечание к ADR-0012 ниже.
  - **SELECT GATE:** показан топ-**20** (человек попросил **15–20**). Человек выбрал **17 = «Делаем»**
    (пропустил `feat-5` `/review`, `feat-6` мини-сводку очереди, `feat-7` сравнение демо бок-о-бок);
    режим дренажа = **Автономно**.
  - **DISPATCH:** записан `.workflow/dispatch-queue.json` (`autonomous: true`, `baseCommit aa37629`,
    `improveSlug improve-platform-vision`) с **17 pending-айтемами** + 17 брифами под
    `.workflow/tasks/<slug>/` (ADR-0014 последовательная очередь; ADR-0019 автономный дренаж).
    `/improve` **сам** `/feature` не запускает (чистота контекста).
- **17 фич в очереди (порядок диспетча):** 1) `dispatch-queue-cli` — атомарный CLI очереди (`cand-21`);
  2) `workflow-phase-rail` — живой граф фаз + анонс смены (`cand-1`); 3) `workflow-now-pulse` — живой
  пульс «Сейчас происходит» (`cand-2`); 4) `improve-swarm-vote-viz` — визуализация роя/голосования
  SCOUT·CONSENSUS (`cand-3`); 5) `dispatch-queue-schema-validation` — валидация схемы очереди +
  contract-тест (`cand-22`); 6) `gate-dispatch-preview` — превью набора фич к диспетчу на SELECT GATE
  (`cand-10`); 7) `dashboard-polling-efficiency` — гейт скрытой вкладки + кэш settings + инкрем. чат
  (`cand-37`); 8) `server-security-headers` — `frame-ancestors` + `Referrer-Policy` (`cand-45`);
  9) `hub-live-now-command-center` — живой «Сейчас» по всем задачам в хабе (`cand-8`); 10) `test-command`
  — `/test` генерация тестов и закрытие пробелов покрытия (`cand-13`); 11) `drain-recovery-stale-failed`
  — восстановление дренажа: залипший in-progress + эскалация failed (`cand-23`); 12) `start-intent-router`
  — `/start` конверсационный роутер намерения (`cand-19`); 13) `review-gate-atomic-writes` — атомарная
  запись review-гейтов в автономном дренаже (`cand-25`); 14) `artifacts-panel-tab` — вкладка «Артефакты»,
  галерея выходов агента с CSP-бронёй (`cand-4`, аналог Claude-Artifacts); 15) `trace-incremental-rebuild`
  — инкрементальный пересчёт вкладки «Трейсинг» (`cand-33`); 16) `docs-command` — `/docs` генерация/рефреш
  доков + аудит дрейфа док↔код (`cand-15`); 17) `artifacts-versioning-diff` — версионирование артефактов +
  дифф версий, тонкий срез (`cand-5`).
- **Почему здесь нет нового ADR:** механика рой/консенсус/выбор/очередь/автономный-дренаж уже покрыта
  ADR-0012/0013/0014/0019 — прогон переиспользует их без изменений. Единственная новая нота —
  **brief-driven knob `w_e=0.3`** (амендмент-указатель к ADR-0012, см. ниже); это настройка ручки
  существующей формулы, а не новая развилка. Технические ADR для queued-но-непостроенных фич (живой граф
  фаз, вкладка «Артефакты», `/test`, `/start`, `/docs`, …) пишут **те** диспетченные прогоны `/feature`,
  когда реализуют их — не эта запись.
- **Прецедент (durable):** **эффективностный вес агрегации `/improve` — управляемая брифом ручка.**
  Дефолт ADR-0012 — `w_e=w_r=0.5`. Когда бриф человека явно ставит амбицию выше полировки, оркестратор
  МОЖЕТ понизить `w_e` (здесь до `0.3` при `w_r=0.5`), чтобы дорогие высоко-импактные продуктовые свинги
  не штрафовались усилием из контенции. Записано как `state.aggWeights` прогона; формула ADR-0012 иначе
  не меняется. Это амендмент к ADR-0012 (вес — параметр, не константа), а не новый ADR.
- **Объём этого прогона:** read-only аудит + запись очереди + эта запись в БЗ. **Прод-код здесь не
  менялся.** Бриф/план: `.workflow/tasks/improve-platform-vision/`.

## 2026-06-28 — conditional-get-etag (производительность: ETag/304 для поллинг-эндпоинтов)
- **Что:** диспетченная из `improve-overall-3` фича feat-13 (`cand-9`). `_serve_task_file` (`/data`,
  `/replies`) и роут `/chat` получили **conditional GET**: слабый ETag `W/"<mtime_ns>-<size>"` (хелпер
  `_file_etag`) + `Cache-Control: no-cache`; при совпадении `If-None-Match` → `304` без тела. `_send`
  теперь пускает override `Cache-Control` через `extra_headers`. Тест `tests/test_conditional_get.py`
  (7 кейсов). 0 правок фронта — браузер ревалидирует сам, отдаёт JS закэшированное тело (видится как 200).
- **Зачем:** поллеры (3с/5с) перечитывали и пересылали полное тело каждый тик даже без изменений; на
  неизменном файле теперь бодилесс-304 вместо чтения+пересылки (и без перепарса chat.jsonl).
- **Прод:** `scripts/server.py`. **Тест:** `tests/test_conditional_get.py`. Известный остаток (не в этой
  задаче): offset-tail кэш парсинга `/chat` для редкого «изменённого» тика (ETag покрыл частый неизменный).
  Проверено: 243 теста зелёные, lint OK.

## 2026-06-28 — attach-magic-byte-check (безопасность: проверка сигнатуры изображения в /attach)
- **Что:** диспетченная из `improve-overall-3` фича feat-8 (`cand-39`). `/attach` (`_attach`) после
  decode и до записи проверяет **magic-bytes** декодированных байт против заявленного `ext`
  (PNG `\x89PNG\r\n\x1a\n`, JPEG `\xff\xd8\xff`, GIF `GIF87a/89a`, WebP `RIFF....WEBP`) — модульный
  хелпер `image_magic_ok(ext, data)`; несовпадение → `400 "format"`, файл не пишется. Тесты в
  `tests/test_attach.py` (mismatch→400, не-PNG GIF→200).
- **Зачем:** `_attach` доверял клиентскому mime и не проверял сами байты → под `att-*.png` можно было
  положить произвольный груз, который оркестратор затем `Read`'ит как изображение. Defence-in-depth.
- **Прод:** `scripts/server.py`. **Тест:** `tests/test_attach.py`. Контракты `/attach` (allow-list mime,
  кап, traversal-гард) сохранены. Проверено: 235 тестов зелёные, lint OK.

## 2026-06-28 — read-body-size-cap (надёжность/безопасность: глобальный кап тела POST)
- **Что:** диспетченная из `improve-overall-3` фича feat-6 (`cand-15`). `Handler._read_body`
  (`scripts/server.py`) получил **глобальный кап** `MAX_BODY_BYTES` (8 МБ, выше base64-раздутого
  attach-макс): `Content-Length > cap` → `413` **до** чтения тела (закрываем соединение, не аллоцируем);
  тело теперь дочитывается циклом до `Content-Length`, недополученное → `400 incomplete body` (а не
  молчаливый `{}`); битый JSON остаётся graceful `{}`. `do_POST` останавливается на `None`.
  Тест `tests/test_read_body.py` (6 кейсов).
- **Зачем:** `rfile.read(length)` без верхнего лимита на ВСЕХ POST → forged `Content-Length` = unbounded
  аллокация (DoS/OOM companion-процесса); кап 5 МБ из ADR-0020 жил только в `/attach` и срабатывал уже
  ПОСЛЕ чтения в RAM. Теперь один гард защищает все маршруты.
- **Прод:** `scripts/server.py`. **Тест:** `tests/test_read_body.py`. `/attach` свой decoded-кап
  сохранил. Проверено: 233 теста зелёные, lint OK.

## 2026-06-27 — atomic-write-lang-attachment (надёжность: закрыть долг ADR-0021)
- **Что:** диспетченная из `improve-overall-3` фича feat-5 (`cand-13`). `write_lang`
  (`scripts/server.py`) и `_attach` (запись байтов вложения) переведены с фиксированного/pid-only
  `.tmp` + голого `os.replace` на общие `_aipf.atomic_temp_name` + `_aipf.atomic_replace` (тот же путь,
  что `Workspace.write_json`). Неточный комментарий `~:144` (ссылался на `write_lang` как образец)
  поправлен.
- **Зачем:** именно эти два писателя — «известный долг» ADR-0021: под параллельными прогонами (общий
  store) их голый `os.replace` ловил транзиентный Windows-`PermissionError(13)` без ретрая → потеря
  записи языка / сбой аплоада. Теперь оба покрыты retrying-replace.
- **Прод:** `scripts/server.py`. **Доки:** ADR-0021 (долг помечен закрытым; остаток — sweep `*.tmp`).
  Проверено: `python dev.py test` зелёный (227), grep подтвердил — голых `os.replace`/фиксированных
  `.tmp` в `server.py` не осталось.

## 2026-06-27 — preview-live-dashboard-parity (улучшение: паритет превью ↔ реальный дашборд; SEED)
- **Что:** диспетченная из `improve-overall-3` фича feat-2 (`cand-19`, ранг #2) — обязательный seed
  пользователя «вид превью всегда точно соответствует реальному дашборду при запуске агентов». Три меры:
  - **Тест паритета** (`tests/test_preview_fixtures.py::PreviewParityTest`): `preview.install()` в
    изолированном tempfile-корне (monkeypatch `TASKS_DIR` + нейтрализация `_sweep_legacy`, чтобы не
    трогать живой store) → `sha256(index.html) == sha256(templates/dashboard.html)` для каждой фикстуры.
  - **`/health` source** (`scripts/server.py:295`): отдаёт `source` = realpath работающего `server.py`;
    `preview._check_source` (`scripts/preview.py`) предупреждает, если превью-сервер поднят не из репо.
  - **Док-заметка** (`dashboard-i18n.md`): refresh плагина из репо ПЕРЕД сверкой с live.
- **Зачем:** превью и live рендерят дашборд из формально разных источников (репо vs плагин-кэш); штамп
  был идентичен по построению, но ничем не проверялся, а ось репо↔кэш молча давала старый дашборд.
- **Решение задокументировано:** ADR-0024. **Прод:** `scripts/server.py`, `scripts/preview.py`,
  `tests/test_preview_fixtures.py`. Проверено: `python dev.py test` зелёный (227 тестов).

## 2026-06-27 — improve-overall-3 (аудит-прогон: рой→консенсус→выбор→диспетч; 15 фич в очередь)
- **Что:** запущен `/improve overall-3` — полный аудит приложения ai-pathfinder через машину
  рой→консенсус→выбор→диспетч. Эта запись фиксирует **сам аудит-прогон**; правки здесь **не**
  реализуются — они диспетчатся как очередь `/feature` и приземляются в своих прогонах.
  - **SCOUT:** 7 read-only `wf-improver`-скаутов (по призме на каждого: UX, производительность,
    надёжность, тех-долг, DX, функциональные пробелы, доступность+безопасность) дали ~50 сырых кандидатов.
  - **CONSENSUS:** оркестратор консолидировал+дедупнул до **40 кандидатов** (`cand-1…cand-40`); 3 voter'а
    `wf-improver` независимо оценили весь список; оркестратор агрегировал **детерминированно**
    (`w_e = w_r = 0.5`, drop `keep == 0`) — поток «панель + арифметика» из ADR-0012.
  - **SELECT GATE:** показан топ-**15** (пользователь явно попросил **top-K = 15**, а не дефолтные 6–8).
    Человек выбрал **все 15 = «Делаем»**; режим дренажа = **Автономно**.
  - **DISPATCH:** записан `.workflow/dispatch-queue.json` (`autonomous: true`, `baseCommit 9a663c3`,
    `improveSlug improve-overall-3`) с 15 pending-айтемами + 15 брифами под `.workflow/tasks/<slug>/`
    (ADR-0014 последовательная очередь; ADR-0019 автономный дренаж). `/improve` **сам** `/feature` не
    запускает.
- **Сид (по требованию пользователя):** ранг-2 **паритет preview↔live дашборда** (`cand-19`) — конкретный
  корень дрейфа, на который указал пользователь: превью рисует дашборд из репо, а живой прогон копирует
  шаблон из плагин-кэша, и теста паритета между ними нет. Планируемый фикс: install-тест по sha256 +
  self-report источника в `/health` + обновлённая доковая заметка. Содержательный ADR про инвариант
  паритета напишет **тот** диспетченный прогон `/feature`, **не** эта запись.
- **15 фич в очереди (по рангу):** 1) фидбэк на провал submit/approve гейта (`cand-2`); 2) паритет
  preview↔live дашборда — СИД (`cand-19`); 3) индикатор потери соединения (`cand-1`); 4) баннер
  неотправленных правок (`cand-5`); 5) закрыть долг ADR-0021 атомарной записи — `write_lang` + attachment
  (`cand-13`); 6) глобальный кап тела `_read_body` / гард от DoS (`cand-15`); 7) управление фокусом оверлея
  (`cand-37`); 8) проверка magic-byte в `/attach` (`cand-39`); 9) кросс-файловый гард дрейфа STR
  (`cand-20`); 10) anchored-индикатор `needsAnswer` между вкладками (`cand-6`); 11) онбординг пустого хаба
  (`cand-4`); 12) семантика ARIA для табов (`cand-38`); 13) conditional-GET ETag/304 (`cand-9`);
  14) инкрементальный append чата (`cand-12`); 15) smoke-тест паритета HUB_PAGE↔dashboard (`cand-25`).
- **Почему здесь нет нового ADR:** механика рой/консенсус/выбор/очередь/автономный-дренаж уже покрыта
  ADR-0012/0013/0014/0019. Этот прогон переиспользует их без изменений — на уровне аудита нового решения
  не принято, поэтому ADR не добавляется (и не дублируется). Технические ADR (напр. инвариант
  preview-паритета) принадлежат диспетченным прогонам `/feature`.
- **Объём этого прогона:** read-only аудит + запись очереди + эта запись в БЗ. Прод-код здесь не менялся.

## 2026-06-26 — design-task-sidebar (выравнивание бокового индикатора + перенос now-line)
- **Что:** UI/UX-правка дашборда (`templates/dashboard.html`) через `/design`. Две находки (обе HIGH,
  prism «раскладка и адаптивность»), обе применены:
  - **f1 — выравнивание сайдбара и контента:** внутренние панели (`.page-main > .wrap`) заново брали
    `padding-top:28px` от `.wrap`, а первая карточка добавляла `margin-top:24px` → контент начинался на
    ~52px ниже сайдбара во всех табах. Убран верхний padding панелей (`padding: 0 0 64px`) + обнулён
    `margin-top` первого элемента (`.page-main > main > :first-child`). Кромки совпали (delta 52→0).
  - **f2 — перенос строки «Now: …»:** `.now-line` был `display:flex; align-items:center` → короткая метка
    «Now:» центрировалась напротив середины многострочного значения. Сделан текстовым блоком
    (`overflow-wrap:anywhere`) — метка инлайн, значение переносится ровно.
- **Зачем именно так:** вёрстка per-task дашборда живёт только в шаблоне (агенты копируют его дословно в
  `index.html`, пишут лишь `dashboard.json`) — фикс шаблона и есть фикс генерации, отдельной логики нет.
- **Проверка:** превью-харнесс из worktree + Playwright (delta 52→0 на 4 табах; now-line flex→block).
- **Релиз:** бамп `plugin.json` `0.23.0 → 0.23.1` (патч; `marketplace.json` не трогался).
- **Демо:** `.workflow/tasks/design-task-sidebar/mockups/redesign.html` (бейджи ①② + тогл «До/После»).

## 2026-06-25 — request-language-wins (язык запроса человека побеждает eng-first-дефолт)
- **Что:** Конвенция языка вывода агента переписана: **язык вывода = язык запроса человека** (авто-детект),
  а не глобальная eng-first-настройка. Резолв `state.json.lang` на INTAKE во всех 4 скиллах
  (`feature`/`improve`/`ask`/`new-product`) изменён с «читать `settings.json`» на «авто-детект языка
  запроса; настройка — лишь фолбэк для autonomous/eval-прогонов и язык хрома UI». `lang` теперь управляет
  ВСЕМ human-facing выводом: нарративом оркестратора в терминале, артефактами (brief/plan/questions/
  summary/PRD), дашбордом, текстами гейтов/опциями, чатом/реплаями.
  - Правки: `skills/*/SKILL.md` (секции «Output language»), `skills/*/phases.md` (шаг INTAKE-резолва
    языка), `skills/*/state-schema.md` (поле `lang`), `skills/new-product/loop.md` (коммит фазы),
    `agents/wf-documenter.md` (база знаний — английская независимо от `lang`), `docs/knowledge/conventions.md`,
    `areas/orchestrator-skills.md`, `areas/dashboard-i18n.md`, `README.md`.
- **Зачем:** Жалоба пользователя — при русскоязычном запросе оркестратор вёл диалог и писал артефакты
  по-английски (eng-first дефолт ADR-0018), русский подхватывали только реплаи в дашборде. Теперь агент
  «следит за языком запроса и отвечает на нём» во всех каналах, которые читает человек.
- **Граница (по требованию пользователя):** `docs/knowledge/**`, README и git-коммиты остаются
  **английскими** независимо от языка запроса (durable-артефакты проекта; consistency > язык сессии),
  если человек явно не попросит иначе. Машинно-парсимые заголовки/ключи (`cand:`, секции дайджестов,
  схемы) — английские всегда.
- **Инфраструктура i18n не тронута:** конфиг `settings.json`, эндпоинты `/settings.json` / `POST
  /settings`, словарь фронта и переключатель EN/RU (ADR-0018) работают как раньше — изменилась только
  точка резолва `lang` и конвенция классификации текстов.
- **ADR-0022** — амендмент к части 3/6 ADR-0018. Бамп плагина 0.21.0→0.22.0.
- **Применение:** правки `skills/**`/`agents/**` видны рантайму **только после refresh плагина**
  (ADR-0017: рантайм крутит кэш-копию). В репо проверяется лишь корректность правок.

## 2026-06-25 — design-command (пятая команда `/design` — фокусный UI/UX-аудит одного компонента)
- **Что:** добавлена **пятая** slash-команда плагина — **`/design <компонент>`**: глубокий UI/UX-аудит
  **ОДНОГО** выбранного компонента интерфейса с реализацией одобренных правок. Машина стадий
  `INTAKE (имя и/или скриншот) → AUDIT (рой ds-auditor по 7 UI/UX-призмам) → COMPOSE (оркестратор сводит
  находки в ОДИН аннотированный мокап) → CONSENT GATE (чек-лист по находкам, feat-K-стиль, дефолт
  «Применить» + approve-plan) → IMPLEMENT (ds-coder) → VERIFY (wf-reviewer + /code-review) → DONE`.
  Новые ассеты: `skills/design/*` (SKILL.md + reference-файлы) и ростер `ds-*` (`agents/ds-auditor.md`,
  `agents/ds-coder.md`) — их пишут другие work-streams; этот WS — **только доки + `plugin.json`**.
- **Ключевые решения (фиксированы, → ADR-0023):**
  - **Единый аннотированный демо** вместо N pick-one демо: один `mockups/redesign.html`, стиль «Вариант А»
    (нумерованные бейджи ①②③ + легенда «что изменилось» + тогл «До/После»). Редизайн одного компонента —
    набор точечных правок, а не альтернативные дизайны на выбор; pick-one был бы ложной моделью. Демо
    самодостаточно под CSP, без CDN. **0 правок `server.py`** — ride-the-contract (lineage ADR-0008/0013/0016).
  - **Гейт согласия — дефолт opt-out «Применить»** (нет ответа = применяется): намеренная **инверсия**
    opt-in выбора фич в `/improve` (человек уже сам указал компонент). Та же машина гейта (`feat-K`/`f<k>`:
    `questions[choice]`+`approve-plan`, ADR-0013), обратный дефолт.
  - **Новый ростер `ds-*` без пина модели:** `ds-auditor` (read-only, без Write/Edit) + `ds-coder`
    (Write/Edit) — обе наследуют модель сессии. **Два файла из-за разного tool-set**, не из-за модели; реюз
    `wf-*` невозможен (контракты под EXPLORE/IMPLEMENT, модель глобальна для `subagent_type`; lineage ADR-0006).
  - **Вход компонента — имя и/или скриншот** (скриншот через вложения дашборда, ADR-0020; оркестратор
    `Read`'ит сохранённый файл).
  - **VERIFY — только `/code-review`** (не `/security-review`): фокусные UI/UX-правки компонента редко несут
    поверхность безопасности.
- **Зачем именно так:** бэкенд агностичен к содержимому items (ADR-0008/0013/0016) → находки/демо/гейт
  ложатся на существующие поля (`planBlocks`/`questions[choice]`/`demo`/`approve-plan`) без новых
  эндпоинтов; `/design` — **пятый** потребитель ride-the-contract. Единый аннотированный демо честнее N
  демо для редизайна одного компонента и прямо ложится на per-finding гейт (бейдж ①②③ ↔ карточка `f<k>`).
- **Объём этого WS (доки + витрина):** новый `decisions/ADR-0023-design-command-single-annotated-demo.md`;
  обновлены `areas/orchestrator-skills.md` (карта `/design` + ростер `ds-*` + ключевые файлы + футер),
  `README.md` (раздел `/design` + строка Layout), `INDEX.md` (строка ADR-0023 + футер), эта запись;
  бамп `plugin.json` `0.22.0 → 0.23.0` (`marketplace.json` не трогался — новые ассеты подключаются по
  конвенции каталогов, бамп косметичен).
- **Коллизия нумерации ADR:** задача изначально взяла `0022`, но при интеграции в `main` номер занял
  `ADR-0022-request-language-wins` (влит параллельно), поэтому перенумеровано в **0023** (и версия — в
  `0.23.0`, т.к. `0.22.0` выпущен под request-language).
- **План:** `.workflow/tasks/design-command/plan.md`
- **ADR:** `decisions/ADR-0023-design-command-single-annotated-demo.md`

## 2026-06-25 — ci-lint-gate-wire (CI-job `lint`: stdlib-only + no-CDN реально проверяются)
- **Что:** в `.github/workflows/ci.yml` добавлен отдельный job `lint` (`ubuntu-latest`, Python 3.13,
  шаг `python scripts/check_stdlib.py`). Теперь несущие инварианты **stdlib-only** (`scripts/*.py`) и
  **no-CDN** (`templates/*.html`) проверяются в CI на каждый push/PR, а не только локально через
  `dev.py lint`. Существующий job `test` (matrix 3 ОС × 3 Python, `unittest discover`) не тронут.
- **Зачем:** гейт `scripts/check_stdlib.py` существовал с задачи `stdlib-invariant-lint-gate`
  (2026-06-19), но в CI **не был подключён** — инварианты держались на дисциплине ревьюера и локальном
  запуске. Эта задача замыкает связку: нарушение (не-stdlib импорт или CDN-ссылка) теперь валит CI.
- **Уточнение к записи 2026-06-19 (строка ~281):** там сказано «Подхватывается `dev.py lint` (feat-5) и
  CI (feat-1) — связка замыкается». На момент той записи CI-часть была заявлена, но **фактически job в
  `ci.yml` отсутствовал**. С этой задачей (`ci-lint-gate-wire`, дренаж очереди `improve-overall-2`,
  feat-1 / cand-27) утверждение становится истинным: job `lint` подключён.
- **Доки:** `README.md:254` (убрано устаревшее «stub» про CI), `docs/knowledge/conventions.md` (CI-раздел
  описывает оба job'а — `test` и `lint`).
- **Проверка:** гейт зелёный (exit 0) на чистом дереве; красный (exit 1) ловит и не-stdlib импорт, и
  CDN-ссылку; 183 теста `unittest` зелёные. Ревью-гейты (code/security) — без находок.
- **Без ADR** — мелкая механическая правка конфигурации CI (подключение уже существующего гейта), не
  новое архитектурное решение.
## 2026-06-25 — atomic-write-pid-temp (кросс-платформенная атомарная запись под конкуренцию)
- **Что:** Центральная атомарная запись стала безопасной при **конкурентных писателях в общий store**.
  - `scripts/_aipf.py`: `atomic_write` теперь пишет через **per-process temp** `atomic_temp_name(path)`
    (`:65`) = `path.<pid>.<uuid8>.tmp` вместо общего `path.tmp` (убирает гонку на temp), публикует через
    новый `atomic_replace(tmp, path)` (`:76`) — `os.replace` с **ограниченным ретраем** (50×2 мс) **только**
    на транзиентную Windows-`PermissionError(13)`, на исчерпании **re-raise**. При любой `OSError` чистит
    осиротевший temp и **re-raise**'ит исходную ошибку (без тихого фолбэка). `write_json` чинится транзитивно.
  - `scripts/server.py`: `Workspace.write_json` (`:141`) **переиспользует** те же `_aipf`-хелперы
    (`atomic_temp_name`/`atomic_replace`) — два центральных писателя больше не дрейфуют; убран лишний
    `import uuid`.
  - `tests/test_atomic_write.py` (новый, **10 тестов**): round-trip обоих писателей, отсутствие
    осиротевших temp, очистка+re-raise на ошибке записи, конкурентная запись **N процессов/потоков** в
    один путь.
- **Зачем:** параллельные `/feature` делят один store через симлинк (ADR-0010) — несколько процессов и
  потоков сервера пишут один и тот же файл (`state.json`/`draft.json`/`server.json`/`settings.json`).
  Прежняя схема `path.tmp` + `os.replace` (1) давала гонку на общий temp (полу-записанный файл) и
  (2) случайно падала на Windows, где `os.replace` атомарен по результату, но кидает транзиентную
  `PermissionError`, если destination в этот момент держит другой писатель. Нужны **обе** меры:
  per-process temp **И** ретрай replace.
- **Ключевой инвариант (в ADR):** кросс-платформенная атомарная запись с конкурентами требует
  per-process temp + ограниченного ретрая `os.replace` на транзиентную Windows-`PermissionError`. Это
  **переиспользуемый** инвариант для всех будущих писателей поверх общего store — брать
  `_aipf.atomic_temp_name`/`atomic_replace`, не катать свой temp+replace.
- **Известный долг (зафиксирован, НЕ реализован):** `server.write_lang` (`scripts/server.py:209`,
  `path + ".%d.tmp" % pid` + голый `os.replace`) и attachment-upload (`scripts/server.py:1372`,
  `path + ".tmp"`) ещё **не** на общем хелпере (без ретрая) — подтянуть отдельной задачей; комментарий
  ~`scripts/server.py:145` ссылается на `write_lang` как на образец — теперь неточно. Плюс sweep старых
  осиротевших `*.tmp`.
- **Источник:** дренаж аудита `improve-overall-2`, feat-2 (cand-14).
- **Verify:** 193 теста зелёные (10 новых), ревью-гейты без блокеров.
- **ADR:** `decisions/ADR-0021-cross-platform-atomic-write-pid-temp-retry.md`. Конвенции дополнены
  (`conventions.md` §«Полезные утилиты» — `atomic_write`/`atomic_replace`/`atomic_temp_name` + инвариант).
## 2026-06-25 — changes-endpoint-test (регресс-тест бэкенда вкладки «Изменения»)
- **Что:** новый `tests/test_changes.py` (14 кейсов) — **первый** регресс-тест для
  `Handler._build_changes` (`scripts/server.py`, модель вкладки «Изменения»:
  `{base, files:[{path, added, removed, status, untracked}], notGit}`). Реальный git в tempdir под
  `@skipUnless(git_available())`, прямой вызов через `_make_handler` (`Handler.__new__` без HTTP/сокета,
  по образцу `tests/test_hub.py`), мимо 2-с кеша `_changes`. **Прод-код НЕ менялся** (тест-only).
  Покрыто: modified(+/-) с точными числами · added-untracked · deleted · untracked-каталог развёрнут
  `-uall` (а не строка `docs/`) · noise-фильтр 0-байтного untracked · сортировка по пути ·
  rename(R) · `baseCommit≠HEAD` (diff против старого коммита) · битый/несуществующий base → fallback HEAD ·
  ведущий-`-` base reject к HEAD (без падения) · absent base → HEAD · `worktreePath` направляет diff в
  чужое рабочее дерево · не-git каталог → `notGit`/пустой список · graceful-обёртка `_changes` (любая
  ошибка `_build_changes` → error-модель, не 500).
- **Кросс-платформенность (по `conventions.md` §«Кросс-платформенность тестов»):** forward-slash в путях
  модели независимо от ОС; явный LF при записи фикстур (`newline="\n"`, иначе CRLF собьёт `_count_lines`);
  `os.path.realpath(tempfile.mkdtemp())` (tempdir-симлинк на macOS/Windows vs каноничный путь git);
  локальный `git config user.*`/`commit.gpgsign=false`, чтобы не зависеть от глобального конфига.
- **Зачем:** `_build_changes` — единственный нетривиальный git-парсер бэкенда (numstat × porcelain-status,
  renames, untracked-развёртка, выбор базы) и переиспользуется knowledge-графом, но регресса не имел —
  ловушки из area-доки (quotePath, `-uall`, ` => `/` -> `, noise) держались только на ревью. Тест
  фиксирует контракт модели как исполнимую спецификацию.
- **Проверка:** 14 ran / 0 skipped; полный набор 197 OK; ревью-гейты без блокеров.
- **Источник:** дренаж аудита `improve-overall-2`, feat-3 (cand-29).
- **Известный долг (наблюдения, НЕ чинилось в этой задаче — прод не трогали):**
  - ⚠ **Хрупкий rename-парсинг** `rest.split(" -> ")[-1]` (`scripts/server.py:789`): при
    `core.quotePath=false` уязвим к имени файла, содержащему литерал ` -> `. Тест покрывает только
    happy-path rename — патологическое имя не проверяется.
  - ⚠ **`_base_commit` гоняет `cat-file -e base^{commit}` в cwd воркфри** (`scripts/server.py:723`):
    `baseCommit`, существующий в main, но **не** в worktree, молча падает в HEAD (различие main↔worktree
    при общем store не учтено).
  - **Тест-нит:** `test_rename_staged` имеет условный `skipTest` (если git не классифицировал staged `mv`
    как `R`); `test_not_git_dir` избыточно помечен `@skipUnless(git_available())`.
- **Без ADR** — это **добавление теста**, новых архитектурных решений нет. (Заодно сознательно избегается
  коллизия нумерации ADR между параллельными ветками фич дренажа.)
- **Доки:** эта запись + строка «покрыт тестом» в `areas/dashboard-changes-tab.md`.
## 2026-06-25 — state-json-corrupt-recovery (битый state.json не теряется молча)
- **Что:** `scripts/worktree.py` — `_write_state_fields` больше **не затирает молча** непарсимый
  `state.json`. Новый хелпер `_quarantine_corrupt_state` переименовывает битый файл в
  `state.json.corrupt-<TS>` (`<TS>` = `%Y%m%d-%H%M%S`+pid, **без двоеточий** — NTFS запрещает `:`,
  поэтому `now_iso` тут непригоден; перенос через атомарный кросс-платформенный `os.replace`).
  `_write_state_fields` различает **три исходных состояния** файла через предчтение
  `os.path.exists` **ДО** `read_json` (graceful-дефолт `read_json` не даёт отличить «нет файла» от
  «битый»): `merged` (валидный dict — поля дозаписаны), `recovered` (битый → quarantine + warning +
  свежий минимальный state), `created` (файла не было). Доп. статус `overwritten` — если quarantine
  не удался (`os.replace` упал): файл перезаписан, печатается **честный** warning «не удалось
  сохранить», а не ложный note, будто оригинал лежит рядом. `cmd_add` печатает разный note по статусу.
- **Контракт возврата изменён:** `_write_state_fields` теперь возвращает **`(state, status:str)`**
  (`status ∈ {created, recovered, merged, overwritten}`) вместо прежнего `(state, created:bool)`;
  единственный вызывающий `cmd_add` обновлён под новый контракт. **`read_json` НЕ менялся** —
  graceful-контракт сохранён (на него опирается `test_corrupt_json_is_graceful`).
- **Зачем:** при гонке `add`↔INTAKE битый/полузаписанный `state.json` (снимок оркестратора:
  `phase`/`iteration`/`dispatched`/`questions`) молча минтился заново поверх — INTAKE-снимок терялся
  без следа. Теперь оригинал всегда уходит в карантин рядом, прежде чем создаётся свежий минимальный.
- **Тесты:** `tests/test_worktree.py` — класс `WriteStateFieldsCorruptTest` (3 кейса: corrupt / missing
  / valid). Verify: **186** тестов зелёные; ревью-гейты без блокеров (одна LOW исправлена).
- **Источник:** дренаж аудита `improve-overall-2`, feat-4 (кандидат `cand-15`).
- **Follow-up (зафиксированный долг, НЕ реализован):**
  - **`.bak` авто-откат** — `_write_state_fields` спасает оригинал в `.corrupt-<TS>`, но не
    восстанавливает поля из last-good перед затиранием; авто-откат — отдельная задача.
  - **Симметрично укрепить читателей `state.json`** (`telemetry_hook`, `_hub_run`) — сейчас они не
    деструктивны (graceful через `read_json`), отдельный follow-up.
- **Доки:** обновлена `areas/parallel-runs-hub.md` (новое поведение `_write_state_fields`: различение
  missing/corrupt/valid, карантин `.corrupt-<TS>`, статусы, контракт `(state, status)` + два инварианта
  + два follow-up'а); ориентир на функцию заменил устаревший номер строки. `INDEX.md` — футер.
- **ADR:** нет — поведенческая правка одной функции (writer стал недеструктивным), не новая
  архитектурная развилка; во избежание коллизии нумерации ADR между параллельными ветками не заводим.
  Инвариант «не терять `state.json`» зафиксирован в area-доке и этой записи.
- **План:** `.workflow/tasks/state-json-corrupt-recovery/plan.md`
## 2026-06-25 — gate-pick-in-place (инлайн-рендер SELECT GATE «Вариант B»)
- **Что:** реализован ранее задокументированный **«Вариант B»** SELECT GATE'а `/improve`
  (`templates/dashboard.html`). `choice`-вопрос, чей `id` совпадает с `id` карточки `planBlocks` (на гейте
  `/improve` это `feat-K`), теперь рисуется **инлайн в подвале своей карточки** — над `regionFooter` —
  через хелпер `choiceInput(item, ans, reps)` (`dashboard.html:1679-1680`). В цикле «Вопросы» такой
  вопрос **пропускается** (`continue`, `:1697`), а пустая карточка «Вопросы» не рендерится (`if(shown)`,
  `:1703`). Радио выбора фичи теперь сидит **под её брифом**, а не отдельным дублирующим списком.
- **Привязка строго по `id`+`kind`:** `planIds.has(id) && kind === "choice"` (`:1671`/`:1697`/`:1679`),
  **не** по префиксу `feat-`. Любая карточка, чей id совпал с `choice`-вопросом, получает инлайн-радио;
  всё остальное не затронуто.
- **Обратная совместимость:** так как сопоставление по id+`choice` (а не по имени), drain-mode `/feature`
  и гейты плана `/feature` // `/new-product` рендерятся как раньше (их вопросы либо `open`, либо их id не
  совпадают с id `planBlocks`). Любой `dashboard.json` без коллизии id рендерится без изменений.
- **Объём:** **0 правок сервера**, `wireBlocks`/`saveAnswer`/`[data-answer]`/`POST /draft` не тронуты —
  чисто аддитивная ветка рендера (контракт ADR-0008/0013 — бэкенд агностичен к содержимому items).
  Контракт Варианта A (общий `feat-K`, свободный ответ, «нет ответа = Пропускаем», порядок Submit→Approve)
  не изменён — изменилась только раскладка на экране.
- **Проверка:** `check_stdlib` exit 0, `test_settings` + общий набор зелёные, **headless DOM-проверка**
  подтвердила инлайн-рендер выбора в подвале карточки.
- **Источник:** дренаж аудита `improve-overall-2`, feat-5 (cand-1).
- **Доки:** обновлён **`skills/improve/dashboard-guide.md`** (§SELECT GATE — раздел «Future upgrade
  (Variant B) — not implemented now» переписан в «Inline-choice render (Variant B) — now implemented»:
  фактическое поведение + блок «Backward compatibility»; язык файла английский сохранён). `INDEX.md`
  футер освежён.
- **Замечание о копиях `dashboard-guide.md`:** у `/feature` и `/new-product` есть свои
  `skills/*/dashboard-guide.md`, но раздела «Variant B» в них нет (он был уникален для SELECT GATE
  `/improve`) — расхождения по этому разделу нет, их копии в этой задаче **не трогали** (область — гейт
  `/improve`). При этом сама правка `dashboard.html` **глобальна** (рендер общий для всех скиллов):
  поведение «инлайн-choice по совпадению id с карточкой `planBlocks`» теперь действует для любого
  дашборда, где такая коллизия id возникнет.
- **ADR:** не заводился — это реализация **ранее задокументированного** Варианта B (ADR-0013 уже описал
  развилку A vs B), плюс во избежание коллизии нумерации ADR между параллельными ветками.
## 2026-06-25 — gate-dispatch-counter (чип «N из M отмечено Делаем» на SELECT GATE)
- **Что:** на гейте плана `/improve` (фаза SELECT GATE) в actionbar дашборда добавлен **чип**
  `#dispatch-chip` «**N** из **M** отмечено Делаем». Чисто-фронтендовая аддитивная фича
  (`templates/dashboard.html`), **0 правок сервера** — бэкенд агностичен к содержимому items (ADR-0008).
- **Формула (`updateDispatchChip`, `templates/dashboard.html:2042`):**
  - **M (`total`)** = `choice`-вопросы, у которых id матчит `^feat-\d+$` **И** есть одноимённая карточка
    в `planBlocks` (`questions.filter(q => q.kind==="choice" && /^feat-\d+$/.test(q.id) &&
    planBlocks.some(b => b.id===q.id))`). Так считаются только feat-K фичи гейта; **обычные `q1`/`q2`**
    и **drain-mode** вопросы (без парной planBlock-карточки) в M **не попадают**.
  - **N (`done`)** = из этих M — те, на которые в `draftItems` есть `answer` (`questionId===q.id`),
    у которого **`text === options[0]`** (первый вариант = «Делаем») **ИЛИ** свободный текст,
    начинающийся на `делаем|делай|do` (`/^(делаем|делай|do)/` по `trim().toLowerCase()`). Нет ответа —
    не в N.
- **Видимость:** чип виден **только** при `draftItems.length > 0 && total > 0`. После Submit
  (`draftItems` пуст) и вне гейта (`total===0`) — `hidden`. Зовётся из `updateQueue()` (`:2039`) на
  каждом тике поллинга вместе со счётчиком очереди.
- **i18n-приём (числа вне `t()`):** локальная `t()` не делает подстановку плейсхолдеров, поэтому числа
  вынесены в **отдельные `<b>`** (`#dispatch-done`/`#dispatch-total`), а словарные ключи
  `actionbar.dispatchOf` («of»/«из») и `actionbar.dispatchCount` («marked Do»/«отмечено Делаем»)
  дают только статичные тексты вокруг них. Чип перестраивается из узла применения языка
  (`templates/dashboard.html:1353`) с сохранением обоих `<b>`. Ключи добавлены в **оба** блока словаря
  `STR.en`/`STR.ru` (инвариант STR-парности, smoke-тест полноты — `areas/dashboard-i18n.md`).
- **Источник:** дренаж аудита `improve-overall-2`, feat-6 (cand-2).
- **Verify:** `check_stdlib.py` exit 0, `tests/test_settings.py` + общий набор зелёные, headless
  DOM-проверка («1 of 2 marked Do», drain-mode-вопрос не засчитан в M).
- **ADR:** нет — мелкая аддитивная FE-фича на существующем контракте (ADR-0008/0013); во избежание
  коллизии нумерации ADR между параллельными ветками новый не заводился. Зафиксировано в area-доке
  `dashboard-feedback-ui.md` (строка про чип) + этой записи.
- **План:** `.workflow/tasks/gate-dispatch-counter/plan.md`
## 2026-06-25 — dashboard-aria-live-regions (первые live-регионы доступности)
- **Что:** Первый заход на доступность дашборда — **только атрибутивная** правка
  `templates/dashboard.html` (+3/-3 строки, **JS/контент не менялись**), чтобы скринридер озвучивал
  динамические изменения:
  - `#status` (обёртка статуса агента в шапке, `:620`) → `role="status" aria-live="polite"
    aria-atomic="true"`; декоративная `.dot` внутри → `aria-hidden="true"`.
  - `#toast` (всплывающие уведомления, `:684`) → `role="status" aria-live="polite" aria-atomic="true"`.
  - `#chat-log` (лента чата, `:674`) → `aria-live="polite" aria-relevant="additions"`.
- **Зачем:** статус агента, тосты и новые реплики чата меняются асинхронно (поллинг/события), но
  скринридер о них молчал — слепой пользователь не узнавал о смене фазы, всплывшем уведомлении или ответе
  агента. `aria-live="polite"` даёт вежливый анонс без перебоя; `aria-atomic` на статусе/тосте — зачитать
  узел целиком; `aria-hidden` снимает шум с декоративного индикатора.
- **Follow-up (известный долг, НЕ реализован):** `renderChat` перерисовывает `#chat-log` полным
  `innerHTML`-replace → `aria-relevant="additions"` **best-effort** (при замене поддерева браузер не
  обязан анонсировать «добавления»). Для надёжного анонса новых реплик нужен **инкрементальный append**
  новых `.msg` либо отдельный визуально-скрытый live-контейнер только для нового текста. Прочие
  динамические узлы (вкладки/табы — семантика+клавиатура) — отдельные кандидаты аудита (cand-41/42), вне
  этой задачи.
- **Источник:** дренаж аудита `improve-overall-2`, feat-7 (cand-40).
- **Проверка:** `check_stdlib` exit 0, 183 теста OK, headless DOM-проверка (3 live-региона присутствуют).
- **Объём:** 0 правок `scripts/*`/контрактов/контента — чисто атрибуты HTML, бэкенд агностичен.
- **Без ADR** — атрибутивная правка, новых архитектурных развилок нет; во избежание коллизии нумерации
  ADR между параллельными ветками. Семантика зафиксирована в `areas/dashboard-feedback-ui.md`
  (секция «Доступность: первые live-регионы»).
## 2026-06-25 — hub-json-mtime-cache (per-task mtime-кэш карточек хаба — устранение big-O ловушки)
- **Что:** Пересборка `/hub.json` теперь **мемоизируется per-task по сигнатуре `(mtime, size)`** трёх
  входных файлов задачи (`telemetry.jsonl`/`state.json`/`dashboard.json`). Дорогой проход
  `_hub_telemetry` (чтение всего `telemetry.jsonl`) + `read_json` выполняются **только для задач,
  чья сигнатура изменилась**; для неизменившихся сырая карточка переиспользуется из кэша дословно.
  Менялся `scripts/server.py` + `tests/test_hub.py`.
  - **Сигнатурный кэш (`scripts/server.py`).** Класс-атрибуты `_hub_card_cache` (`:852`, `slug ->
    {sig, card}`) + `_hub_card_lock` (`:853`). Хелперы: `_stat_sig(path)` (`:910`) — дешёвая
    подпись `(st_mtime, st_size)` без чтения тела, `None` при `OSError` (size добавлен к mtime,
    чтобы поймать append в ту же секунду на ФС с грубой гранулярностью; `telemetry.jsonl` append-only);
    `_hub_signature(slug)` (`:921`) — кортеж из трёх `_stat_sig` в фиксированном порядке. `_hub_run`
    (`:933`) разнесён: сырую карточку без поля `active` строит **новый** `_hub_build_card(slug)`
    (`:955`, бывшее тело `_hub_run`) — она зависит только от файлов и потому кэшируема. `os.stat` и
    тяжёлый билд карточки — **вне лока**; под локом только чтение/запись кэша.
  - **Инвариант «`active` не кэшируется».** Поле `active` зависит от `now` (окно
    `HUB_ACTIVE_WINDOW_SEC`, 24 ч) и **пересчитывается на каждый вызов** из кэшированной сырой
    карточки: `_hub_run` возвращает `dict(card, active=self._hub_is_active(card["phase"],
    card["updatedAt"], now))`. Сырая карточка **никогда не мутируется** (возвращается копия через
    `dict(...)`). Так задача, пересёкшая границу 24 ч без записи в файлы, корректно уезжает в историю,
    даже если её карточка не перестраивалась.
  - **Прунинг.** `_build_hub` (`:887`) после обхода `_list_tasks()` чистит из `_hub_card_cache` slug'и
    исчезнувших задач (`:903–907`, под локом) — кэш не растёт за счёт пропавших задач.
  - **Внешний TTL 3 c оставлен как нижняя граница.** Кэш `_hub` (`:872`, `now + 3.0`) не тронут —
    лежит поверх per-task кэша; сигнатурный кэш экономит проход телеметрии **внутри** пересборки,
    когда TTL `_hub` истёк (≥ интервала поллинга хаба 3 c).
  - **Контракт `/hub.json` НЕ изменён** — те же поля карточки и аналитики; lock-паттерн как у `/trace`.
  - **Тесты `tests/test_hub.py` (`HubCardCacheTest`, 7, `:421`):** reuse без повторного прохода
    телеметрии (счётчик вызовов `_hub_telemetry`), инвалидация по изменению **каждого** из трёх файлов,
    пересчёт `active` от `now` при неизменной карточке, прунинг исчезнувшего slug, идентичность
    контракта. Сброс `_hub_card_cache.clear()` добавлен рядом со всеми сбросами `_hub_cache`.
- **Зачем:** `/hub.json` пересобирал **каждую** карточку (полный проход `telemetry.jsonl` каждой задачи)
  на каждый промах TTL — стоимость росла линейно с числом задач × размером их телеметрии (big-O ловушка
  при накоплении истории). Сигнатурный кэш делает горячий путь пропорциональным числу **изменившихся**
  задач, а не всех; типично меняется 1–2 активные задачи, остальные отдаются из памяти.
- **Источник:** дренаж аудита `improve-overall-2`, feat-8 (`cand-10`).
- **Проверка:** `scripts/check_stdlib.py` exit 0; `tests/test_hub.py` зелёный (32 теста хаба), полный
  прогон 190 зелёный.
- **Доки:** дополнена `areas/parallel-runs-hub.md` (per-task mtime-кэш: сигнатура трёх файлов,
  перепарс только изменившихся, `active` от `now`, прунинг, TTL как нижняя граница; обновлены ориентиры
  по именам функций `_hub`/`_build_hub`/`_hub_run`/`_hub_build_card`/`_hub_telemetry`), `INDEX.md`.
- **ADR:** нет — сигнатурный кэш + now-инвариант это **перф-оптимизация** существующего read-only
  агрегата, без новой архитектурной развилки или смены контракта; инвариант «`active` не кэшируется»
  зафиксирован в area-доке. Решение НЕ заводить ADR принято осознанно во избежание коллизии нумерации
  ADR между параллельными ветками (следующий свободный 0021 берут и другие ветки очереди
  `improve-overall-2`); если позже сочтём решение достойным ADR, оно получит свободный номер при слиянии
  (вероятна переномерация).

## 2026-06-24 — feature-fast-lane (гейт TRIAGE: примитивные задачи мимо тяжёлой машины)
- **Что:** добавлен **гейт TRIAGE (фаза §0)** в `/feature`, который запускается **до INTAKE**. Если
  задача **примитивна** (все условия: один модуль/область — **не по кол-ву файлов**, а по охвату; **нет**
  новой функциональности — фикс/твик существующего поведения; тривиальная проверка; нет решения для
  человека; низкий риск — не деструктивно/безопасность/деньги/миграции/публичный контракт; можно сделать
  напрямую) — она идёт по **Fast Lane**: оркестратор **сам правит файлы** (релакс правила «не пишу код
  сам»), **без** компаньон-
  сервера/дашборда, **без** роя `wf-*`, **без** гейта плана и **без** тяжёлых `/code-review`+
  `/security-review`. Сохраняется только worktree (дешёвая изоляция) + лёгкий захват (одна строка в
  `task-log.md`, если стоит). Всё нетривиальное/неоднозначное/рискованное/широкое → полная машина стадий.
- **Зачем:** пользователь: «примитивные задачи проходят слишком долго — оптимизируй время/шаги». Полная
  машина (сервер, дашборд, EXPLORE→ELABORATE→PLAN GATE→рой кодеров→VERIFY→ревью-гейты) — оверкилл для
  однострочной правки и заставляет человека ждать гейт там, где решать нечего.
- **Клапан эскалации (односторонний `fast→full`):** как только ставка «задача маленькая» рушится
  (расползлась на несколько модулей, оказалась новой функциональностью, нужна нетривиальная проверка,
  всплыл дизайн-выбор, неоднозначность, риск) — оркестратор **останавливается и поднимает
  полную машину** (сервер+дашборд, `lane:"full"`, EXPLORE→ELABORATE→PLAN GATE), сообщив человеку. Никогда
  не «продавливать» усложнившуюся задачу по fast lane.
- **Правки (только доки скилла, без кода/сервера):** `skills/feature/phases.md` (новая «## 0. TRIAGE»),
  `skills/feature/SKILL.md` (ментальная модель + шаг triage в start/resume), `skills/feature/state-schema.md`
  (поле `lane:"fast"|"full"`), `docs/knowledge/areas/orchestrator-skills.md` + `INDEX.md`, бамп версии
  плагина → 0.20.0. Чистая надстройка: полный путь остаётся дефолтом для непримитивных задач.
- **Мета:** саму эту правку провёл по принципу, который она вводит — напрямую, без полной машины.
- **Без ADR** — это пропорциональная надстройка над существующей машиной стадий, не новое архитектурное
  решение; механика — в `phases.md` §0.

## 2026-06-24 — task-page-left-sidebar (шапка задачи → левый сайдбар + слим топ-бар)
- **Что:** чисто-фронтендовый рефактор `templates/dashboard.html` — высокая шапка задачи разбита на
  **левый инфо-сайдбар** + **слим топ-бар**. Введён грид-обёртка `.page-grid wrap wide` (клон
  `.docs-grid`: `260px 1fr`, `gap:16px`, `align-items:start`, коллапс `@media max-width:860px → 1fr`)
  между `</header>` и панелями вкладок. INFO-узлы (`#title #slug #phase #iter #status/#status-text
  #now-line #ws-track #progress-bar(скрыт) #ws-summary #open-threads`) переехали в
  `aside.task-sidebar#task-aside` (стиль `.doc-tree`: `var(--panel)`/`var(--line)`/radius 12 + flex-column),
  панели обёрнуты в `.page-main` (`.page-main > .wrap` сбрасывает `max-width`/`margin` — ширину задаёт грид).
- **Зачем:** топ-бар перерос — пользователь просил «инфо слева, сверху только тема/язык/чат/хаб».
  Сайдбар — **глобальный** (виден на всех вкладках, вне `switchTab`-тоглинга `hidden`), **sticky**
  (`top:92px; max-height:calc(100vh-108px); overflow:auto`).
- **Природа правки:** **только фронтенд**, **0 правок `scripts/server.py`/`dashboard.json`**,
  **0 новых i18n-ключей**, **0 новых theme-токенов** (только реюз `--panel`/`--line` + существующая
  `@media 860px`). Каждый id сохранён → `render()`/`applyStaticStrings()`/`initTheme`/`initLang`/
  `switchTab` **не тронуты** (рендер ключуется по id, а не по позиции в DOM).
- **5 решённых дизайн-вопросов:** (q1) сайдбар **глобальный**, не только на Workflow; (q2)
  `#open-threads` **в сайдбар**; (q3) **логотип остаётся в топ-баре** (стал кликабельной `<a href="/hub">`,
  отдельный `.back-row` убран, текст-ссылка `#hub-link` оставлена рядом); (q4) ширина расширена до
  **1480px** (`.wrap.wide`), чтобы сайдбар не сжимал контент; (q5) сайдбар **sticky**.
- **Ловушка соблюдена:** сайдбар — **сибл `#content`** (через `.page-main`), а НЕ внутри него — иначе
  `render()` затирал бы INFO-узлы при `#content.innerHTML` каждый тик. Margin-auto trap
  (`dashboard-theming.md:84`) учтён — у `.page-grid` нет вертикального margin-шортката.
- **План:** [.workflow/tasks/task-page-left-sidebar/plan.md]. Без ADR — это раскладочный твик в рамках
  существующих примитивов (`.docs-grid`/`.doc-tree`), не новое архитектурное решение.

## 2026-06-24 — dashboard-ui-container-fixes (полировка контейнеров/раскладки FE)
- **Что:** три чисто-вёрсточных фикса раскладки фронтенда (без правок сервера/контрактов):
  - **Issue 1 — паритет ширины шапки хаба (`HUB_PAGE` в `scripts/server.py`).** Раньше `header.top`
    сама была flex-контейнером без `max-width`, и логотип/контролы разъезжались к краям окна. Теперь
    `header.top` — полноширинная полоса (`padding:0`, sticky + `--topbar` + `backdrop-filter:blur`,
    подложка/граница edge-to-edge), а её содержимое (`.brand` + `.topright`) обёрнуто во внутренний
    `header.top .top-inner { max-width:1180px; margin:0 auto; padding:14px 32px; display:flex; … }`.
    Контент шапки встал в ту же 1180px-колонку, что и `.wrap`/`#filter-bar` — как на странице задачи.
  - **Issue 2 — строка «Сейчас: …» центрируется (`templates/dashboard.html`).** Шорткат
    `.top-inner.now-strip { margin:16px 0 14px }` **перетирал** базовый `.top-inner { margin:0 auto }`
    (горизонтальный `auto` → 0), и строка прижималась к левому краю окна. Фикс — `margin:16px auto 14px`
    (вертикальные отступы сохранены, авто-центрирование возвращено).
  - **Issue 3 — «Active runs» в один столбец (`HUB_PAGE`).** Сетка `repeat(3,1fr)` + первая карточка
    `heroCard`(`grid-column:span 2`)/остальные `slimCard` давали разные размеры. Теперь
    `.cards { grid-template-columns:1fr }`, правило `.runcard.hero{grid-column:span 2}` снято, `render()`
    рисует каждый запуск через `heroCard` (единый детальный вид), мёртвый `slimCard` удалён.
- **Зачем:** согласовать визуальные контейнеры хаба и страницы задачи, вернуть статус-строку агента в
  границы шапки и привести активные запуски к единообразному полноширинному списку.
- **Проверка:** 164 теста OK (5 платформенных skip), `server.py` парсится, скриншоты подтвердили все три
  фикса в светлой и тёмной темах (хаб + страница задачи).
- **Примечание:** код фиксов попал на `main` в составе коммита `task-image-attachments` (общее рабочее
  дерево параллельных сессий); этот коммит — KB-документация фиксов.
- **План:** `.workflow/tasks/dashboard-ui-container-fixes/plan.md`
- **Подводный камень:** см. `areas/dashboard-theming.md` — шорткат `margin:<v> 0 <v>` на модификаторе
  `.top-inner` молча убивает базовый `margin:0 auto`; для сохранения центрирования — `margin:<v> auto <v>`.

## 2026-06-24 — task-image-attachments (вложения-изображения к задаче)
- **Что:** Человек может прикрепить **изображения** в дашборде — в чате И в каждом канале комментариев
  (block/variant/thread-reply/select-to-comment) — байты сохраняются под
  `<root>/.workflow/tasks/<slug>/attachments/`, чтобы **оркестратор `Read`'ил файл** (визуальный вход,
  напр. правка UI-бага по скриншоту). Инлайн-превью в дашборде вторично, но в скоупе.
- **Зачем именно так:** транспорт **base64-in-JSON, не multipart** — `cgi` удалён в Python 3.13
  (в CI-матрице), `_read_body` JSON-only; standalone `POST /attach` (декод+валидация+запись+ref) держит
  base64 вне `chat.jsonl` и шарит **один** путь записи на все каналы (все шлют `/chat` через
  `postAnchored()`); `GET /image` зеркалит traversal-гард `_serve_mockup`; ref `images:[{file,name,mime}]`
  едет на строке `/chat` (ключ `images` в allow-list `_chat_post`). Лимиты 5 МБ/изобр., 6/сообщ.,
  png/jpeg/gif/webp (без SVG) — **первый лимит размера тела** запроса. Pending-вложения держатся в
  модуль-переменной (keyed by anchor/`"chat"`), чтобы 3с ре-рендер `#content` их не стёр.
- **Контракт-исключение:** две точки входа + ключ `images` — **осознанное отступление** от ADR-0008
  «0 правок сервера / агностичный бэкенд», зафиксировано в **ADR-0020**.
- **Док-апдейт (ws-docs, b8):** `areas/dashboard-feedback-ui.md` (раздел «Вложения-изображения» +
  **исправлена устаревшая заметка** «комменты → `/draft {kind:"comment"}»: на деле через
  `postAnchored()`→`/chat`, `/draft` — только `open`/`choice` + выбор варианта), новый
  `decisions/ADR-0020-image-attachments-base64-attach-endpoint.md`, обновлён `INDEX.md`. План —
  `.workflow/tasks/task-image-attachments/plan.md`.

## 2026-06-19 — dashboard-i18n (i18n дашборда/хаба + глобальный язык + конвенция языка агента)
- **Что:** Интернационализация UI (**eng-first**, переключатель `en`/`ru`) + глобальная языковая
  настройка плагина + смена языковой конвенции агента. Три части:
  - **Сервер (`scripts/server.py`, ws1):** хелперы `settings_path()`/`read_lang()`/`write_lang()`;
    глобальный конфиг `~/.claude/ai-pathfinder/settings.json` (`{"lang":"en","ts":...}`, дефолт `en`,
    белый список `{en,ru}`); `GET /settings.json` (без кэша, единый источник) + `POST /settings` (ветка
    **ДО** проверки slug — `do_POST` иначе отвергает запрос без slug 400). Хаб пишет настройку через POST.
  - **Фронт (`templates/dashboard.html` + `HUB_PAGE`/`INDEX_LANDING` в `server.py`, ws2/ws3):**
    инлайн-словарь `STR{en,ru}` (плоские английские ключи) + `t(key)`; контрол флип-иконка `#lang-btn`
    (EN⇄RU) рядом с `#theme-btn`; логика `storedLang/resolveLang/applyLang/initLang` по образцу темы
    (ADR-0015); источник истины — `/settings.json` (fetch+поллинг для кросс-страничного синка); дефолт
    жёсткий `en`. Контент агента из `dashboard.json` НЕ локализуется. Словарь дублирован в двух файлах
    (риск дрейфа) → smoke-тест полноты ключей. Канонический перевод лейблов —
    `.workflow/tasks/dashboard-i18n/i18n-glossary.md`.
  - **Конвенция агента (`skills/**`, `agents/**`, `README.md`, ws4):** жёсткое «human-facing → Russian»
    заменено на «дефолт = глобальная настройка (`en` при отсутствии); в чате/реплаях и `/ask`-ответе —
    язык вопроса (авто-детект перебивает дефолт)»; машинные заголовки/ключи остаются английскими;
    INTAKE-шаг чтения настройки → `state.json.lang` → под-агентам. README переведён на английский.
  - **Тесты + знания (ws5):** `tests/test_settings.py` (GET дефолт/чтение, POST валид/невалид/без-slug,
    изоляция `~`); smoke-тест полноты словаря; ADR-0018, область, эта запись, INDEX.
- **Зачем:** UI был целиком русским без i18n-слоя, конвенция жёстко требовала русский — это противоречило
  eng-first и авто-детекту языка вопроса. Глобальная настройка даёт один язык на плагин, управляемый из
  хаба; единый no-store канал `/settings.json` синкает хаб и дашборд.
- **Ключевые решения:** глобальный конфиг в `~/.claude/` (не per-project/`.workflow`, не плагин-рут —
  кэш-копия); единый `/settings.json` без кэша (vs дозапись в кэшируемый `/hub.json`); `POST /settings`
  до проверки slug; eng-first дефолт; словарь+`t()` по образцу темы + дубль в двух файлах + smoke-тест
  полноты; классификация текстов (язык вопроса для чата/`/ask` vs дефолт для артефактов, машинные
  заголовки стабильны); применение к рантайму только после refresh плагина — **ADR-0018** (связь с
  ADR-0015 образец / ADR-0017 кэш-копия / ADR-0010 store / ADR-0016 /ask / ADR-0012 парсер).
- **План:** `.workflow/tasks/dashboard-i18n/plan.md`
- **ADR:** `decisions/ADR-0018-global-language-setting-i18n-and-agent-language-convention.md`
- **Область:** `areas/dashboard-i18n.md` (новая; ссылка из `areas/dashboard-theming.md`); конвенция языка
  обновлена в `conventions.md` и `areas/orchestrator-skills.md`.

## 2026-06-18 — server-reliability (надёжность companion-сервера)
- **Что:** Устранён «завал осиротевших серверов» дашборда (на машине жили 5+ `server.py` по 3–6 дней,
  а `server.json` указывал на труп → симптом «сервер постоянно падает»).
  - **Идемпотентный singleton:** `main()` переиспользует живой сервер для корня (`server_is_live`) и
    выходит, не плодя дубликат; `--force` обходит (`scripts/server.py`).
  - **Heartbeat:** демон-тред `Heartbeat` раз в `HEARTBEAT_SECS`(5с) обновляет `server.json.ts`;
    `server_info_age`/`SERVER_STALE_SECS`(30с) ловят труп с переиспользованным ОС-pid.
  - **Стабильный порт:** `port_for_root` = `8473 + sha1(realpath(root))[0] % 25` (скан — fallback).
  - **Reap осиротевших:** `discover_servers` (зонд `/health`) + чистый `gc_targets` + `reap_servers`;
    CLI `server.py --gc`. `server.json` и `/health` получили поле `root`.
  - **Очистка на выходе:** `install_shutdown_cleanup` (atexit + SIGTERM/SIGINT) → `clear_server_info`
    удаляет свой `server.json`.
  - **Тесты:** +20 в `tests/test_server_health.py` (age/live/port/gc/health-root); вся сюита 147 зелёная.
  - **Доки/контракты:** `architecture.md` §startup переписан; launch-секция в 4×`feedback-loop.md`
    упрощена до «просто запускай — сервер дедуплицируется»; bump плагина `0.16.0 → 0.17.0`.
- **Зачем:** дашборд — фундамент под все воркфлоу; ненадёжный сервер обесценивал фичи поверх него.
  Лечили не «демонизировать сильнее» (серверы и так переживали сессию — `ppid 1`), а самодедупликацию
  и самоуборку: один сервер на корень, стабильный URL, трупы не копятся.
- **ADR:** `decisions/ADR-0017-server-singleton-heartbeat-stable-port.md`

## 2026-06-17 — ci-cross-platform-tests (фича 1/8 очереди `improve-overall`, feat-1/cand-28)
- **Что:** Оффлайн тест-сьют сделан **кросс-платформенным** + добавлен CI. Чинилась **непортируемость
  тестов**, НЕ баги продукта — прод-код не тронут.
  - `tests/test_worktree.py`: хелпер `_symlink_supported()` (проба `os.symlink` в tmp) + константа
    `_SYMLINKS` + `@skipUnless` на 3 symlink-теста (skip **по реальной способности**, чтобы Developer
    Mode/CI с поддержкой симлинков не глушить); `ListWorktreesPorcelainTest` строит пути от
    `os.path.realpath(tempfile.mkdtemp())` и сравнивает через `os.path.abspath(...)` — фикс
    `/repo/main` (POSIX-литерал) vs `C:\repo\main`.
  - `tests/test_feed.py`: `newline=""` в фикстурах jsonl — CRLF→строго LF, иначе байтовый курсор
    `_iter_lines_from` (`scripts/_aipf.py:381`) сбивается на лишнем `\r` под Windows.
  - `tests/test_server_health.py`: `@skipIf(os.name == "nt")` на 2 dead-pid теста — на Windows
    `os.kill(pid, 0)` даёт `OSError(WinError 87)`, не `ProcessLookupError`; продукт намеренно
    консервативен (любой не-`ProcessLookupError` = «жив») — это **не трогали**, скипнули тест.
  - `.github/workflows/ci.yml` (новый): matrix `os: [ubuntu/macos/windows-latest] × python: [3.11,3.12,3.13]`,
    `on: [push, pull_request]`, `fail-fast: false`, единственный шаг
    `python -m unittest discover -s tests` — stdlib-only, без `pip`.
- **Зачем:** часть оффлайн-тестов падала на Windows из-за непортируемых допущений (POSIX-пути,
  `\n`-фикстуры под байтовый курсор, разная pid-семантика, безусловные симлинки), и регрессии на
  чужих ОС были невидимы — CI-матрицы не было. Теперь набор честно зелёный на 3×ОС × 3×Python.
- **Источник:** очередь `/improve` (прогон `improve-overall`, кандидат `cand-28`), фича 1/8 (feat-1),
  призма DX. Бриф: `.workflow/tasks/ci-cross-platform-tests/brief.md`.
- **Доки:** `conventions.md` — секция «Тесты» дополнена под-разделами **«Кросс-платформенность тестов»**
  (skip по реальной способности vs `os.name`; `skipIf(nt)` для pid-семантики; `newline=""` для jsonl;
  пути от `tempfile`/`realpath` + `abspath`) и **«CI»** (матрица, stdlib-only без pip).
- **ADR:** нет — это **конвенции тестов + стандартный CI**, не архитектурное решение. Паттерны
  зафиксированы в `conventions.md`; новой развилки/инварианта продукта не вводилось (продукт не менялся).

## 2026-06-17 — improve-overall (прогон `/improve`, аудит → очередь из 8 фич)
- **Что обследовали:** сквозной аудит **всего приложения** ai-pathfinder через `/improve` по **7 призмам**
  (UX/продукт, перформанс, надёжность, техдолг, DX, пробелы функциональности, a11y+безопасность). Код
  проекта НЕ правился — `/improve` только обследует и ставит в очередь (механика — ADR-0012/0013/0014).
- **Воронка:** рой 7 scout-аналитиков `wf-improver` → **46 находок** → дедуп оркестратором →
  **35 кандидатов** `cand-1…cand-35` (`.workflow/tasks/improve-overall/candidates.md`, id стабильны) →
  панель 3 vote (независимая оценка всего списка) → детерминированная агрегация
  `score=(mean(imp)−0.5·mean(eff)−0.5·mean(rsk))·mean(conf)/3`, отброс `keep==0`, **топ-8**.
- **Гейт:** человек отметил **все 8** как «Делаем» → очередь `.workflow/dispatch-queue.json`
  (`mode:"sequential-feature"`, ADR-0014), по `brief.md` на фичу. `/improve` сам `/feature` не запускает.
- **Очередь (8 фич, ранжированно, n · slug · призма · cand):**
  1. `ci-cross-platform-tests` · DX · cand-28 — CI + кросс-платформенные тесты (7 тестов падают на Windows).
  2. `approve-button-submit-guard` · UX · cand-1 — защита approve без submit.
  3. `pause-polling-when-hidden` · perf · cand-13 — пауза поллинга при `document.hidden`.
  4. `markdown-url-sanitize-xss` · security · cand-33 — блок `javascript:`-схем в md-ссылках.
  5. `dev-py-cross-platform-runner` · DX · cand-29 — `dev.py` вместо POSIX-only Makefile.
  6. `gate-ranking-visibility` · пробелы · cand-8 — строка рейтинга `votes[]` в карточках `feat-K`.
  7. `actionbar-order-hint` · UX · cand-3 — хинт порядка Submit→Approve на actionbar.
  8. `stdlib-invariant-lint-gate` · DX · cand-31 — `check_stdlib.py` + grep CDN.
  > Эти 8 — **только в очереди**, НЕ реализованы. Реализация — отдельными `/feature`-прогонами.
- **Повтор кейса draft.json-fallback:** человек нажал «Утвердить план», пропустив «Отправить» — выбор
  всех 8 жил только в несабмиченном `draft.json` (вне `READABLE_FILES`). Оркестратор прочитал его
  **напрямую с диска** (полон/однозначен) вместо переспроса — ровно сценарий, уже задокументированный
  **уточнением к ADR-0013** (2026-06-15). Новый ADR не нужен: поведение покрыто, кейс просто встретился снова.
- **План:** `.workflow/tasks/improve-overall/plan.md`
- **ADR:** нет (следовали ADR-0012/0013/0014; архитектурно нового не решено).
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
## 2026-06-18 — pause-polling-when-hidden (фича 3/8 из очереди `improve-overall`)
- **Что:** Гейт фронтового поллинга по `document.hidden` (`templates/dashboard.html`): ранний
  `return` в `traceTick` (`:1374`), `changesTick` (`:1141`), `chatTick` (`:2289`) когда вкладка
  скрыта; `visibilitychange` (`:2343`) при возврате делает догоняющие вызовы `chatTick()` +
  активного таб-тика (trace/changes).
- **Важный нюанс:** главный `tick` (`:2331`, `/data`) **намеренно НЕ гейчен** — он нужен фоновому
  awaiting-уведомлению (`:795`, Notification срабатывает именно при `document.hidden`). Гейт всех 4
  тиков (как предлагал scout cand-13) сломал бы это уведомление. Гейтим только тяжёлые
  (`/trace` перечитывает транскрипты, `/changes` — git diff) + лёгкий chat.
- **Решения человека:** q1 — главный tick оставить как есть; q2 — chatTick гейтить.
## 2026-06-18 — markdown-url-sanitize-xss (фича 4/8 из очереди `improve-overall`)
- **Что:** Санитизация схемы URL в markdown-ссылках (`templates/dashboard.html:582`, функция `inline`):
  строковый `.replace(... href="$2")` заменён на функциональный replacer с allow-list схем —
  `http(s)`/`mailto` + относительные/якоря; `javascript:`/`data:`/`vbscript:` и protocol-relative
  `//host` → `href="#"`. Логика проверена 13 кейсами (node).
- **Зачем:** Закрывает stored-XSS: `dashboard.json` агент пишет из недоверённых источников, а
  `[клик](javascript:…)` исполнял JS в origin дашборда. Единственный активный контент вне sandbox-iframe.
- **Нюанс:** mockup-ссылки (`:914/:916`) безопасны (относительный `/mockup?...` + `encodeURIComponent`),
  не трогали. `inline` — единственная точка рендера markdown-ссылок, фикс покрывает весь `md()`.
- **Решения человека:** q1 — `href="#"` для запрещённой схемы; q2 — блокировать `//host`.
- **Объём:** 0 правок `scripts/*`. ADR не нужен.
## 2026-06-19 — dev-py-cross-platform-runner (фича 5/8 из очереди `improve-overall`)
- **Что:** Новый `dev.py` (stdlib, `argparse`) — кросс-платформенный раннер: `test [цели]`
  (`sys.executable -m unittest`, discover или цели), `serve` (проброс `--port/--open/--no-browser/--no-forward`
  в `scripts/server.py --root <ROOT>`), `lint` (стаб: зовёт `scripts/check_stdlib.py` если есть, иначе
  сообщение про feat-8). ROOT = каталог `dev.py`; `subprocess.call` + проброс кодов возврата. README
  «## Development» дополнен кросс-платформенным путём.
- **Зачем:** `Makefile` POSIX-only (`python3`/`$$(pwd)`/требует `make`) — на Windows DX-слой не работал.
  `sys.executable` снимает зависимость от `make` и имени интерпретатора.
- **Решения человека:** q1 — включить `lint`-стаб (forward-совместимо с feat-8); q2 — `test` прокидывает цели.
- **Объём:** `Makefile` и `scripts/*` не тронуты (dev.py дополняет). ADR не нужен.
## 2026-06-19 — gate-ranking-visibility (фича 6/8 из очереди `improve-overall`)
- **Что:** Контракт-правка инструкций `/improve` (markdown): в `skills/improve/phases.md` (§PROPOSE)
  и `skills/improve/dashboard-guide.md` (§SELECT GATE) добавлена **обязательная строка рейтинга** в
  карточку `feat-K` — компактно `score X.XX · согласие N% · impact·effort·risk a·b·c` (числа, без
  vote-note) из `state.json.votes[]`.
- **Зачем:** `consensus.md:99` требует «legible ranking — not a black box», но контракт карточки этого
  не закреплял — человек выбирал вслепую к уже посчитанным агрегатам. cand-8 из аудита.
- **Решения человека:** q1 — компактная единая строка; q2 — без vote-note.
- **Объём:** 0 правок `server.py`/`dashboard.html`/тестов — markdown в существующем поле `body`
  (ADR-0013, бэкенд агностичен). Формат согласован с `consensus.md`. ADR не нужен.
## 2026-06-19 — actionbar-order-hint (фича 7/8 из очереди `improve-overall`)
- **Что:** Подсказка порядка действий на actionbar (`templates/dashboard.html`): HTML-элемент
  `.actionbar-hint` («Отметьте фичи → «Отправить» → «Утвердить». Без ответа = Пропускаем.») первым
  ребёнком `.actionbar-inner` + 2 CSS-правила (цвет `--warn`, скрытие вне awaiting через
  `.actionbar:not(.awaiting) .actionbar-hint{display:none}`).
- **Зачем:** Инструкция о порядке Submit→Approve жила только в `summary` (надо прокрутить); хинт у
  кнопок (закон близости) снижает шанс пропустить шаг. cand-3 из аудита.
- **Нюанс:** 0 JS — видимость даёт существующий тоггл `.awaiting` (`:803`), вешаемый по
  `status==="awaiting-batch"`.
- **Решения человека:** q1 — инлайн первым ребёнком; q2 — цвет `--warn`.
- **Объём:** 0 правок `scripts/*`. ADR не нужен.
## 2026-06-19 — stdlib-invariant-lint-gate (фича 8/8 из очереди `improve-overall` — очередь дренирована)
- **Что:** Новый `scripts/check_stdlib.py` (stdlib: ast/glob/re) — исполнимый гейт двух инвариантов:
  (1) `scripts/*.py` импортируют только stdlib (allowlist = `sys.stdlib_module_names` ∪ локальные стемы
  `*.py` в scripts/, относит. импорты пропущены — `_aipf` легитимен); (2) `templates/*.html` без CDN
  (таргетно: внешние `src/href` на `http(s)://`/`//` + `@import url(http…)`). exit 1 при нарушениях.
- **Зачем:** Инварианты `conventions.md:27,29` держались на дисциплине ревьюера; теперь — гейт.
  Подхватывается `dev.py lint` (feat-5) и CI (feat-1) — связка замыкается.
- **Решения человека:** q1 — проверять только `scripts/`; q2 — таргетная CDN-проверка.
- **Проверка:** позитив (чистый репо → 0) + негатив (`import requests` и `<script src=//…>` → 1).
- **Объём:** один новый файл, `scripts/server.py` и пр. не тронуты. ADR не нужен.

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
