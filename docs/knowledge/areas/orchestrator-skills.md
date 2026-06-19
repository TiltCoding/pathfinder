# Область: Скиллы-оркестраторы и ростеры под-агентов

> Как в этом плагине устроены команды-оркестраторы (`/feature`, `/new-product`, `/improve`, `/ask`) и их
> под-агенты: регистрация конвенцией каталогов, паттерн «SKILL.md + reference-файлы», frontmatter
> агентов (включая `model:`) и почему ростеры `wf-*` и `np-*` раздельны. Эта область раньше
> отсутствовала в базе знаний — следующий агент не должен заново выводить устройство оркестраторов из
> кода (флажок из `.workflow/tasks/new-product-workflow/exploration.md:160`).

## Назначение

Плагин ai-pathfinder реализует **slash-команды как скиллы-оркестраторы**. Главный агент (оркестратор)
не пишет код сам — он исполняет машину стадий и спавнит специализированных под-агентов через Agent tool.
Четыре команды:

- **`/feature`** — работа в **существующей** кодовой базе: EXPLORE кода → план → один проход IMPLEMENT
  (`skills/feature/SKILL.md:14`).
- **`/new-product`** — **greenfield** с нуля: DISCOVER (элиситация + ресёрч) → PRD → план фаз →
  эволюционный build-loop (`skills/new-product/SKILL.md:15`). Сиблинг `/feature`; отличие — стартовая
  точка (пустой репозиторий vs существующий код).
- **`/improve`** — **производитель** feature-прогонов (не редактор кода): рой аналитиков обследует
  существующее приложение → консенсус голосующей панели → человек выбирает фичи → посев параллельных
  `/feature`-прогонов в git-worktree (`skills/improve/SKILL.md:16`). Сиблинг `/feature`/`/new-product`;
  отличие — **ничего не реализует сам**, а готовит и раздаёт задачи для `/feature` (см. секцию ниже).
- **`/ask`** — **read-only вопрос-ответ** (не редактор, не производитель): по вопросу человека мини-рой
  read-only `ask-researcher` обследует доки и код → оркестратор консолидирует дайджесты и **сам
  синтезирует** текстовый ответ + инфографику + схему процесса → держит **чат** для дальнейших вопросов
  (`skills/ask/SKILL.md:1`). Сиблинг остальных трёх; отличие — **ничего не правит в репозитории вообще**:
  это самый лёгкий оркестратор (нет гейта плана, нет IMPLEMENT/VERIFY); единственная правка кода во всём
  воркфлоу — опциональный бейдж `kind:"ask"` в хабе (см. ADR-0016). Подробнее — секция ниже.

## Ключевые файлы

- `skills/feature/SKILL.md:1`, `skills/new-product/SKILL.md:1`, `skills/improve/SKILL.md:1`,
  `skills/ask/SKILL.md:1` — корни оркестраторов: frontmatter (`name`, `description`) + тело (ментальная
  модель, таблица под-агентов, start/resume, operating rules, телеметрия).
- `skills/new-product/phases.md`, `skills/new-product/loop.md`,
  `skills/new-product/feedback-loop.md`, `skills/new-product/dashboard-guide.md`,
  `skills/new-product/state-schema.md`, `skills/new-product/knowledge-guide.md` — reference-файлы
  оркестратора `/new-product` (детали стадий, цикла, сервера, рендера, state, базы знаний). Список и
  семантика «читай по мере надобности» — `skills/new-product/SKILL.md:47`. Зеркало feature-набора в
  `skills/feature/`.
- `skills/improve/phases.md:1`, `skills/improve/consensus.md:1`, `skills/improve/dashboard-guide.md:1`,
  `skills/improve/state-schema.md:1`, `skills/improve/feedback-loop.md`, `skills/improve/parallel.md`,
  `skills/improve/knowledge-guide.md` — reference-файлы `/improve`. Доменные новые: `phases.md` (машина
  стадий INTAKE…DONE), `consensus.md` (рой → дедуп → vote-панель → детерминированная агрегация →
  seed-and-handoff). Переносимые (копии из `skills/feature/` с точечной адаптацией): `feedback-loop.md`,
  `parallel.md`, `knowledge-guide.md` — почти дословно; `state-schema.md` и `dashboard-guide.md` —
  с добавленными секциями (improve-поля state, контракт `feat-K` SELECT GATE). Список «читай по
  надобности» — `skills/improve/SKILL.md:50`.
- `skills/ask/SKILL.md:1`, `skills/ask/phases.md:1`, `skills/ask/dashboard-guide.md:1`,
  `skills/ask/feedback-loop.md:1`, `skills/ask/state-schema.md:1`, `skills/ask/knowledge-guide.md:1` —
  reference-файлы `/ask`. Доменный — `phases.md` (машина стадий `INTAKE→RESEARCH→SYNTHESIZE→ANSWER→DONE`,
  контракт `demo`-визуализаций, авто-DONE). Переносимые (урезанные копии из `skills/improve/`):
  `dashboard-guide.md` (без §SELECT GATE; `summary`/`planBlocks`/`demo`/чат/трейсинг), `feedback-loop.md`
  (центр — секция **Chat**; гейта/`approve-plan` нет), `state-schema.md` (+ask-поля `kind`/`questionLog[]`/
  `lastChatTs`), `knowledge-guide.md` (как есть, для DONE). **Не копируются** `consensus.md`/
  `dispatch-queue.md`/`loop.md`/`parallel.md` — рой `/ask` без голосования/очереди.
- `agents/np-thinker.md:1`, `agents/np-researcher.md:1`, `agents/np-coder.md`, `agents/np-judge.md` —
  ростер `np-*` для `/new-product` (с полем `model:`).
- `agents/wf-explorer.md:1`, `agents/wf-planner.md:1`, `agents/wf-coder.md:1`,
  `agents/wf-reviewer.md:1`, `agents/wf-documenter.md:1`, `agents/wf-improver.md:1` — ростер `wf-*`
  (без `model:`, дефолтная модель сессии). `wf-explorer/planner/coder/reviewer/documenter` —
  для `/feature`; `wf-improver` — двухрежимный (scout/vote) аналитик для `/improve`.
  `wf-reviewer`/`wf-documenter` переиспользуются `/new-product` и `/improve` как есть.
- `agents/ask-researcher.md:1` — read-only исследователь для `/ask` (**без `model:`**, как `wf-*`;
  `tools: Read, Grep, Glob, Bash` — без Write/Edit, структурная гарантия read-only). Покрывает **одну
  грань** вопроса, читает `INDEX.md` первым, возвращает **структурированный дайджест** под синтез
  оркестратором (секции `## Ответ`/`## Опорные источники`/`## Шаги рассуждения`/`## Числа/связи`/
  `## Уверенность/пробелы`), пишет его в `research/<n>.md`. **Не рисует HTML/SVG и никого не спавнит.**
  Собственный файл (а не реюз `wf-explorer`), т.к. `description`/Output `wf-explorer` заточены под
  `exploration.md`/EXPLORE, а `/ask` нужны машинно-парсимые секции (числа для инфографики, шаги для схемы);
  `wf-documenter` `/ask` реюзит как есть на DONE.
- `.claude-plugin/plugin.json:1` — манифест плагина: **только метаданные**, никакого перечисления
  скиллов/агентов/команд.
- `README.md:39` — раздел про `/new-product`; раздел про `/improve` (рой/консенсус/выбор/fan-out);
  раздел про `/ask` (read-only Q&A → визуальный ответ → чат); раздел «Layout» (канонические каталоги,
  включая `skills/improve/`, `skills/ask/`, `wf-improver`, `ask-researcher`).

## Регистрация: конвенция каталогов (не перечисление в манифесте)

`.claude-plugin/plugin.json` содержит **только** `name`, `version`, `description`, `author`, `keywords`
(полностью — `.claude-plugin/plugin.json:1`). Ключей `skills`, `agents`, `commands`, `mcpServers` там
**нет**. Значит ассеты подключаются **по конвенции каталогов**, а не по списку в манифесте:

- **Скилл = slash-команда.** Каталог `skills/<command>/SKILL.md` ⇒ команда `<command>`. Из плагина она
  видна как `/ai-pathfinder:<command>`: пространство имён — поле `name` в `.claude-plugin/plugin.json:2`
  (`"ai-pathfinder"`), имя команды — поле `name` во frontmatter SKILL.md. Каталога `commands/` в репо
  нет — команды приходят из `skills/`.
- **Агент.** Файл `agents/<name>.md` ⇒ под-агент с `subagent_type = <name>`. Тоже по местоположению.
- **Хуки** — единственное, что требует собственный файл-манифест (`hooks/hooks.json`), но и он
  подхватывается по конвенции пути, а не из `plugin.json`.

**Следствие:** чтобы добавить команду, достаточно положить `skills/<command>/SKILL.md` (+ опц.
`agents/<name>.md`). Правки `plugin.json`/`marketplace.json` функционально **не нужны** — только
косметика витрины (бамп `version`, `keywords`, `description`). Новые ассеты появляются после
**переустановки/refresh** плагина (`/plugin install ai-pathfinder@tiltcoding`), а не «на лету» в уже
запущенной сессии.

## Паттерн «SKILL.md + reference-файлы» (высота изложения)

Тело скилла держится **на высоте**: ментальная модель + правила + ссылки на reference-файлы «читай,
когда дошёл до соответствующей части» (`skills/feature/SKILL.md:35`, `skills/new-product/SKILL.md:47`).
Конкретика выносится в короткие kebab-файлы рядом с SKILL.md:

| reference-файл       | что внутри                                                             |
|----------------------|-----------------------------------------------------------------------|
| `phases.md`          | пошаговая машина стадий: что делать на каждой стадии, кого спавнить    |
| `loop.md`            | (только `/new-product`) ядро эволюционного build-loop                  |
| `feedback-loop.md`   | запуск компаньон-сервера, long-poll `/wait`, батчи, `reviews.json`     |
| `dashboard-guide.md` | модель рендера `dashboard.json` (никогда не править HTML руками)       |
| `state-schema.md`    | форма `state.json` для resume                                         |
| `knowledge-guide.md` | структура `docs/knowledge/`, которую растит документер                 |

`feedback-loop.md`/`dashboard-guide.md`/`state-schema.md`/`knowledge-guide.md` почти переносимы дословно
между командами — они про механику сервера/дашборда/state, не про домен. Доменная специфика
`/new-product` живёт в `phases.md` (стадии) и `loop.md` (цикл), плюс в таблице под-агентов SKILL.md.

## Frontmatter под-агента (включая `model:`)

Файл `agents/<name>.md` — frontmatter + английское тело (роль/процедура/выход; артефакты пишутся на
языке глобальной настройки, в чате — на языке вопроса; см. инвариант языка ниже и ADR-0018). Поля
frontmatter:

- **`name`** = `subagent_type`, по нему оркестратор спавнит агента (`agents/wf-coder.md:2`,
  `agents/np-thinker.md:2`). Глобален в плагине — двух агентов с одинаковым `name` быть не может.
- **`description`** — когда применять (драйвит выбор/триггер агента).
- **`tools`** — список разрешённых инструментов через запятую (`agents/wf-coder.md:4`,
  `agents/np-thinker.md:5`). Набор инструментов — это **структурная гарантия роли**: напр. у
  `np-thinker` стоит ровно `Read, Write, Edit` (без Grep/Glob/Bash/Web) — так «мыслитель физически не
  читает сырьё» (`agents/np-thinker.md:5`, `agents/np-thinker.md:14`); у read-only ролей
  (`wf-reviewer`, `np-judge`) нет Write/Edit.
- **`model:`** — модель под-агента (**только у ростера `np-*`**: `agents/np-thinker.md:4` = `fable`,
  `agents/np-researcher.md:4` = `opus`). У ростера `wf-*` поля нет — `/feature`/`/improve` идут на
  дефолтной модели сессии. Значения — **алиасы** (`fable`/`opus`), не полные id. **Прямое следствие
  инварианта «model глобальна для subagent_type»:** `wf-improver` обслуживает оба режима (scout/vote)
  **одним** файлом без `model:` (`agents/wf-improver.md:4`) — две модели потребовали бы второго файла
  (`wf-voter`); режим выбирается **промптом оркестратора**, не моделью.

## Инварианты

- **`model` глобален для `subagent_type`.** Модель задаётся файлом агента и **едина для всех вызовов**
  этого `subagent_type` — переопределить её per-вызов нельзя. Отсюда главное архитектурное следствие:
  нельзя переиспользовать `wf-coder` и одновременно пиннить ему другую модель (правка `wf-*` поменяла бы
  модель и для `/feature`) — нужен **отдельный файл** `np-coder` (см. ADR-0006).
- **Субагенты не спавнят субагентов.** Agent tool доступен только оркестратору. Поэтому **все хэндоффы
  мёдиирует оркестратор**: исследователь вернул дайджест → оркестратор сохранил его → передал мыслителю;
  кодер вернул код → оркестратор прогнал тесты → сбрифовал судей. Прямых каналов агент↔агент нет
  (`skills/new-product/SKILL.md:98`). Из-за этого мыслитель получает **только курированные выжимки**,
  никогда сырьё.
- **`name` уникален в плагине.** Новые роли берут уникальные имена (`np-*`), нельзя завести второй
  `wf-coder`.
- **`description` команд не должны пересекаться.** Триггеры `/feature` и `/new-product` разведены явно:
  `/new-product` несёт «greenfield / from scratch / new product / PRD» и оговорку «NOT for adding a
  feature to an existing codebase — use the feature skill» (`skills/new-product/SKILL.md:3`); у
  `/feature` — «large/existing codebase» (`skills/feature/SKILL.md:3`). Пересечение фраз → движок может
  выбрать не тот скилл. У `/ask` `description` тоже разведён: «read-only Q&A about code/docs» + триггеры
  («как устроено…», «почему…», «где…», «explain», «how does … work») + оговорки «**NOT** for editing code
  (→ feature); **NOT** for a prioritized improvement backlog (→ improve); **NOT** greenfield
  (→ new-product)». Без этого «как работает X» уехало бы в `/feature` (`skills/ask/SKILL.md:3`).
- **Регистрация только при (пере)установке** — не «на лету» в идущей сессии.
- **Инструкции — английские; язык вывода человеку = глобальная настройка плагина**
  (`~/.claude/ai-pathfinder/settings.json`, `en` при отсутствии — eng-first), **а в чате/реплаях язык
  вопроса перебивает дефолт** (авто-детект). Заменило прежнее жёсткое «human-facing → Russian» (задача
  `dashboard-i18n`, ADR-0018). INTAKE-шаг скилла читает настройку → пишет резолвленный язык в `state.json`
  → передаёт под-агентам в промпте спавна. **Машинно-парсимые заголовки/ключи** (секции `ask-researcher`,
  `cand:`-ключи `wf-improver`, схемы дайджестов) остаются **английскими** — иначе ломается парсер
  оркестратора. Подробно — `areas/dashboard-i18n.md` §«Язык агента».

## Подводные камни

- **Реюз агента vs пиннинг модели — взаимоисключающи.** Соблазн «переиспользовать `wf-coder` под
  `/new-product`» ломается о требование пиннить модель: пришлось завести параллельный ростер `np-*` (см.
  ADR-0006). При добавлении новой команды с другими моделями — заводите свой ростер, не правьте чужой.
- **Алиас модели проверяется на спавне.** Неверная строка в `model:` тихо не сработает/упадёт при
  спавне под-агента — значение алиаса (`fable`/`opus`) должно быть актуальным.
- **`/new-product` переиспользует сервер/дашборд/телеметрию/шаблоны как есть.** `slug` per-task уже
  изолирует задачи продукта от задач feature; дублировать `scripts/server.py`,
  `templates/dashboard.html`, общие `templates/artifacts/*` и `templates/knowledge/*` **не нужно**.
- **Гейт-сигнал один на все стадии.** Кнопка `approve-plan` зашита в HTML; `/new-product` интерпретирует
  её **по текущей стадии** (PRD-GATE = «PRD утверждён», PLAN-GATE = «план фаз утверждён»). Это
  сознательное решение «без правок сервера/HTML» (см. ADR-0007). `/improve` трактует тот же сигнал на
  своём единственном гейте как «**диспетчим выбранные фичи**» (`skills/improve/dashboard-guide.md:104`).
- **Гейт `/improve`: обязательный порядок Submit → Approve.** `draft.json` **не** в `READABLE_FILES`
  сервера, поэтому выбор виден оркестратору только **после** «Отправить». Если `approve-plan` пришёл без
  свежего `submissions/<n>.json` — читать нечего, надо переспросить человека сделать Submit
  (`skills/improve/dashboard-guide.md:99`). Дефолт «**нет ответа = Пропускаем**»: фича без `answer` не
  диспетчится (`saveAnswer` игнорит пустой ввод). Оба — контракт скилла, прописаны в `summary` человеку.
- **Посев `/feature`-задачи в worktree чувствителен к порядку.** Сеять `state.json` **после**
  `worktree.py add` через **read-modify-write** (add пишет только `worktreePath`/`branch`/`updatedAt` —
  «whole-write» затрёт их); `baseCommit` снимать **в worktree**, не в main; `checkpoint:"working"` (а не
  `"awaiting-batch"`, иначе резюм зависнет на несуществующем submission); сессию запускать **внутри**
  worktree, иначе атрибуция телеметрии уедет (`skills/improve/consensus.md:174`, `parallel.md`).

## Карта `/new-product`: стадии + build-loop

Стадии (`state.json.phase`, `skills/new-product/SKILL.md:34`):

```
INTAKE → DISCOVER → PRD → PRD-GATE → PHASE-PLAN → PLAN-GATE → BUILD → SHIP → DONE
```

- **Стадия** = шаг воркфлоу (список выше). **Фаза** = вертикальный срез продукта **внутри BUILD**
  (Ф0 walking skeleton, далее фичевые срезы). BUILD идёт по фазам строго по порядку; пошаговая
  механика стадий — `skills/new-product/phases.md`.
- **Два гейта (политика V1):** человек утверждает **PRD** (PRD-GATE) и **план фаз** (PLAN-GATE).
  Всё между ними — включая каждый переход между фазами в BUILD — автономно.
- **Эволюционный build-loop** (на фазу, ядро — `skills/new-product/loop.md`): `np-coder` в режиме
  tests-first материализует тесты из спеки мыслителя → оркестратор замораживает их по хэшу → итерации
  `np-coder` (implement) → прогон тестов → при зелёных 3 параллельных `np-judge` (1 вызов = 1 измерение
  рубрики) → оркестратор детерминированно считает `decision()` (PASS / REFINE / STOP_BUDGET /
  STOP_PLATEAU / ESCALATE). Гибридный гейт «тесты — стена, судья — руль», вердикт-объект, заморозка
  тестов, стоп-условия — см. ADR-0007.

Контраст с `/feature`: там стадии `EXPLORE → ELABORATE → PLAN GATE → IMPLEMENT → VERIFY`, **один**
гейт (план), один проход IMPLEMENT параллельными кодерами, без эволюционного цикла и без судьи
(`skills/feature/SKILL.md:26`).

## Карта `/improve`: рой → консенсус → выбор → диспетч

Третья команда (`skills/improve/SKILL.md:16`). В отличие от `/feature`/`/new-product`, **она не пишет и
не правит код** — это **производитель** feature-прогонов: обследует приложение, ранжирует идеи и **сеет**
их как отдельные `/feature`-задачи в git-worktree. Стадии (`state.json.phase`, `skills/improve/SKILL.md:37`):

```
INTAKE → SCOUT → CONSENSUS → PROPOSE/SELECT GATE → DISPATCH → DONE
```

- **Один гейт = выбор фич** (`skills/improve/SKILL.md:38`, контракт — `skills/improve/dashboard-guide.md:77`).
  Контраст по гейтам: у `/feature` **один** гейт = «утвердить **план**»; у `/new-product` — **два** гейта
  (PRD + план фаз); у `/improve` — **один** гейт, но это «**выбрать фичи**» (что делать), а не «утвердить
  план» (как делать). Всё остальное (рой, голосование, агрегация, посев) — автономно.
- **SCOUT — рой по призмам.** Оркестратор спавнит **7 `wf-improver` в scout-режиме параллельно**, по
  одной на призму: UX/продукт, производительность, надёжность, техдолг, DX, пробелы фич, доступность +
  безопасность (`skills/improve/phases.md:27`). Каждый читает `INDEX.md` первым, ищет проблемы со своей
  призмы и отдаёт кандидатов по схеме `### cand:` (`agents/wf-improver.md:38`). Сырьё → `scout/<prism>.md`.
- **CONSENSUS — голосование + детерминированная агрегация.** Оркестратор консолидирует и **дедуплицирует**
  кандидатов в `cand-1…cand-N` (`candidates.md`), затем спавнит **3 `wf-improver` в vote-режиме
  параллельно**, каждый видит **весь** список и оценивает `impact/effort/risk/confidence` (0–3) + keep/drop
  (`agents/wf-improver.md:60`). Дальше **оркестратор сам** (не LLM) считает балл по формуле
  `score=(mean(impact)−w_e·mean(effort)−w_r·mean(risk))·mean(conf)/3` (дефолты `w=0.5`),
  «согласие»=доля keep, сортирует, берёт **топ-K = 6–8** (`skills/improve/consensus.md:64`). Это
  «панель судей», как у `/new-product` (ADR-0006/0007); подробнее — **ADR-0012**.
- **SELECT GATE — контракт `feat-K`.** Каждое из топ-K = карточка `planBlocks[].id = feat-K` + вопрос
  `questions[kind:"choice"].id = feat-K`, `options:["Делаем","Пропускаем"]` (`skills/improve/dashboard-guide.md:83`).
  Человек: radio → **«Отправить»** (submit) → **«Утвердить план»** (`approve-plan`). **0 правок
  сервера/HTML** — реюз контракта `questions[choice]`+`approve-plan` (ADR-0008); подробнее — **ADR-0013**.
- **DISPATCH — seed-and-handoff.** На каждую фичу с ответом «Делаем»: уникальный slug →
  `worktree.py add` → `baseCommit` в worktree → read-modify-write `state.json` в `EXPLORE`/`working` →
  посев `brief.md`/`dashboard.json`/`index.html` → хаб подхватит автоматически → **человек** запускает
  `/feature` внутри worktree (`skills/improve/consensus.md:110`). Из одной сессии нельзя автозапустить N
  независимых Claude Code-сессий — оркестратор готовит почву, человек заходит. Механика worktree/симлинка/
  хаба переиспользуется как есть (см. `areas/parallel-runs-hub.md`, ADR-0010).
- **DONE.** Финальный `dashboard.json` (карточки запущенных фич + ссылки на их дашборды и `/hub`) +
  `wf-documenter` дописывает базу знаний.

**`/improve` как производитель.** Диспетчнутые `/feature`-прогоны — **отдельные трейсы** (свои slug, свои
worktree), они видны в хабе `/hub`, а не в трейсе задачи `/improve` (`skills/improve/SKILL.md:137`). Сам
`/improve` ничего не коммитит и не имеет стадии VERIFY/`reviews.json` (`skills/improve/dashboard-guide.md:117`).

## Карта `/ask`: мини-рой → консолидация → синтез → чат

Четвёртая команда (`skills/ask/SKILL.md:1`) — **read-only вопрос-ответ**. В отличие от трёх остальных,
**она вообще ничего не меняет в репозитории** (ни код, ни worktree, ни очередь): только читает доки/код и
отвечает текстом + визуализациями + чатом. Это самый лёгкий оркестратор: **нет гейта плана**, **нет
IMPLEMENT/VERIFY**. Стадии (`state.json.phase`, `skills/ask/phases.md:1`):

```
INTAKE → RESEARCH → SYNTHESIZE → ANSWER → DONE
```

- **INTAKE.** Вопрос пишется в `brief.md` (как вопрос, а не спека); `state.json` (`phase:"INTAKE"`,
  `kind:"ask"`, `baseCommit`); старт компаньон-сервера; копия `templates/dashboard.html → index.html`;
  первый `dashboard.json` (`title` = краткая формулировка вопроса, `status:"working"`); URL человеку.
- **RESEARCH — мини-рой по граням.** Оркестратор раскладывает вопрос на **грани** (база знаний/доки,
  серверный код, дашборд/фронт, тесты) и спавнит **несколько `ask-researcher` параллельно** (обычно 2–4;
  узкий вопрос — 1–2), каждый читает `INDEX.md` первым и покрывает **одну грань**, сырьё → `research/<n>.md`.
  Затем оркестратор **консолидирует** дайджесты в единую выжимку. **Без голосования и без очереди** — этим
  рой `/ask` проще роя `/improve` (нет CONSENSUS/панели/`score`, нет DISPATCH-очереди).
- **SYNTHESIZE — рисует сам оркестратор.** У оркестратора есть Write, поэтому он **сам** пишет: (а) текст
  ответа в `summary` (+ опц. `planBlocks` — разбивка ответа); (б) `mockups/infographic.html` (ключевые
  факты/числа/связи из секции `## Числа/связи` дайджестов; инлайн-CSS, без CDN); (в) `mockups/process.svg`
  (как агент пришёл к ответу: `INDEX`/доки → файлы/строки → шаги рассуждения → ответ; статичная, из секций
  `## Шаги рассуждения`/`## Опорные источники`, **не** из живого трейсинга). Обе визуализации подаются
  штатным `demo`-механизмом (`demo.variants[]` → `GET /mockup` в sandbox-iframe) — **0 правок сервера/HTML**.
  Имена файлов под маску `MOCKUP_RE = ^[A-Za-z0-9._-]{1,64}\.(html|svg)$` (латиница/цифры/`._-`).
- **ANSWER — нетерминальная чат-петля.** После первого ответа оркестратор **остаётся** в `ANSWER`,
  припаркован на long-poll `/wait` (`sinceSignal`), слушает сигнал `chat`; на пробуждении читает
  `chat.jsonl`-сообщения с `ts > state.lastChatTs`. Простое уточнение — дозаписью `role:"agent"`; новый
  содержательный вопрос — **новый мини-рой** `ask-researcher` (`research/<n+1>.md`) + консолидация +
  обновление `summary`/`planBlocks` + перегенерация `demo` (`phase` остаётся `ANSWER`); advance
  `lastChatTs`. Нетерминальная фаза **обязательна**: иначе `_hub_is_active` (`server.py:792`) уведёт
  задачу в «Историю» на время чата. Канал `chat.jsonl` агностичен к типу задачи → переиспользуется без
  правок сервера (`exploration-runtime.md §3`).
- **DONE.** **Авто после ~24 ч тишины** в чате (совпадает с окном «активна» хаба) либо по явному запросу
  человека → `phase:"DONE"` (уводит задачу в «Историю» `/hub`); опц. `wf-documenter` дописывает базу знаний.
  В eval-режиме (`AIPF_EVAL=1`) чата нет — после первого ответа сразу `DONE`.

**`/ask` поверх существующего контракта.** Текст = `summary`/`planBlocks`, визуализации = `demo`/`mockups`,
чат = `chat.jsonl`, попадание в хаб — автоматически (каталог `tasks/<slug>/` + `state.json`). Единственная
осознанная правка кода во всём `/ask` — опциональный бейдж `kind:"ask"` в карточке хаба (`_hub_run`/
`runCard`/`histRow` в `scripts/server.py`); задачи без `kind` (`/feature`/`/improve`) рендерятся как раньше
(append-only поле, `conventions.md`). Подробнее — **ADR-0016**, рецепт «новое поле карточки хаба» —
`areas/parallel-runs-hub.md`.

## Как расширять

- **Новая команда-оркестратор:** создать `skills/<command>/SKILL.md` (frontmatter + тело по образцу),
  при необходимости — reference-файлы рядом (`phases.md` и др., копируя переносимые из `skills/feature/`
  или `skills/new-product/`). Развести `description` от существующих команд, чтобы не пересекались
  триггеры. Правки `plugin.json` не требуются (косметика витрины — опционально).
- **Новый под-агент:** создать `agents/<name>.md` с уникальным `name`, нужным `tools` (минимально
  достаточным — набор инструментов и есть гарантия роли) и, если нужна фиксированная модель, полем
  `model: <alias>`. Помнить: модель глобальна для `subagent_type` — для другой модели нужен отдельный
  файл, а не реюз чужого агента.
- **Реюз под-агента в новой команде:** ссылаться на существующий `subagent_type` из таблицы SKILL.md —
  **только если** его модель и инструменты подходят как есть (так `/new-product` и `/improve` реюзят
  `wf-reviewer`/`wf-documenter`).
- **Один агент на несколько режимов (вместо нового файла на режим):** если режимы могут идти на **одной**
  модели и с **одним** tool-set — заводи один файл и различай режим **промптом** оркестратора (так
  `wf-improver` совмещает scout и vote, `agents/wf-improver.md:9`). Отдельный файл нужен только когда
  режимам требуются **разные модели** (`model` глобальна для `subagent_type`) или несовместимые `tools`.

_updated: 2026-06-19 (конвенция языка под глобальную настройку + язык вопроса в чате, ADR-0018)_
