# Область: Скиллы-оркестраторы и ростеры под-агентов

> Как в этом плагине устроены команды-оркестраторы (`/feature`, `/new-product`, `/improve`, `/ask`,
> `/design`, `/test`) + роутер `/start`, и их под-агенты: регистрация конвенцией каталогов, паттерн «SKILL.md + reference-файлы»,
> frontmatter агентов (включая `model:`) и почему ростеры `wf-*`, `np-*` и `ds-*` раздельны. Эта область раньше
> отсутствовала в базе знаний — следующий агент не должен заново выводить устройство оркестраторов из
> кода (флажок из `.workflow/tasks/new-product-workflow/exploration.md:160`).

## Назначение

Плагин ai-pathfinder реализует **slash-команды как скиллы-оркестраторы**. Главный агент (оркестратор)
не пишет код сам — он исполняет машину стадий и спавнит специализированных под-агентов через Agent tool.
Пять команд:

- **`/feature`** — работа в **существующей** кодовой базе: EXPLORE кода → план → один проход IMPLEMENT
  (`skills/feature/SKILL.md:14`). **Перед INTAKE — гейт TRIAGE** (`skills/feature/phases.md` §0): примитивная
  задача (один модуль; **без** новой функциональности; тривиальная проверка; без решения для человека;
  низкий риск) идёт по **Fast Lane** — оркестратор правит сам, без сервера/дашборда, без роя под-агентов
  и без гейта плана. Задача **сложна** (→ полная машина), если затрагивает несколько модулей, добавляет
  функциональность, требует нетривиальной проверки или несёт дизайн-решение/риск — **не по кол-ву файлов**.
  Поле `state.json.lane` (`"fast"|"full"`) фиксирует выбор; односторонний клапан эскалации `fast→full`,
  если задача вдруг оказалась сложной.
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
- **`/design`** — **фокусный UI/UX-аудит ОДНОГО компонента** (не редактор задачи по спеке, не бэклог по
  всему приложению): человек указывает **один** элемент интерфейса (имя и/или скриншот), рой read-only
  `ds-auditor` критикует его по **7 UI/UX-призмам** → оркестратор сводит находки в **ОДИН аннотированный
  демо** → человек на **гейте согласия** отмечает, что применить (дефолт **«Применить»**, opt-out) →
  `ds-coder` реализует одобренные находки → VERIFY (`/code-review`) (`skills/design/SKILL.md:1`). Сиблинг
  остальных четырёх; отличие — **узкий скоуп (один компонент) при широком аудите (много призм)**, и
  **производит** редизайн сам через свой ростер `ds-*`. `description` разведён: «audit/redesign **one**
  component» + оговорки «**NOT** a backlog app-wide (→ improve); **NOT** read-only Q&A (→ ask); **NOT** an
  arbitrary feature build (→ feature)» (`skills/design/SKILL.md:3`). Подробнее — секция ниже.
- **`/test`** — **только-тесты для существующего модуля/области** (не фича, не ревью диффа): человек
  называет цель → `wf-explorer` read-only находит непокрытые ветви/контракты → `wf-planner` собирает
  **план тестов** → **гейт плана** (как у `/feature`) → `wf-coder` пишет `tests/test_*.py` по
  `conventions.md` §tests → VERIFY (**зелёный прогон = гейт** + `wf-reviewer` бракует тавтологии)
  (`skills/test/SKILL.md:1`). **Реюзит ростер `wf-*`** — нового агента нет; **не меняет поведение** кода
  (нашёл баг → не чинит, это `/feature`). `description` разведён от feature/review/improve/ask. Шестая
  команда; регистрируется конвенцией каталога (без правок `plugin.json`).
- **`/start`** — **лёгкий роутер намерения** (не воркфлоу): человек описывает задачу словами, оркестратор
  классифицирует против `description`-полей установленных команд и **рекомендует** нужную (дефолт) или
  **делегирует** одним хэндоффом через Skill-тул, прокидывая язык запроса (`skills/start/SKILL.md:1`).
  **Единственный файл** — `SKILL.md`, без reference-бандла (роутер не гоняет машину/дашборд); 0 новой
  инфраструктуры, **route-not-execute** (сам ничего не исследует/правит — минимальный риск). Седьмая
  команда (роутер). Таблица маршрутизации: Q&A→ask, фича→feature, greenfield→new-product, аудит→improve,
  UI компонента→design, тесты→test, баг→feature, ревью диффа→/code-review.

## Ключевые файлы

- `skills/feature/SKILL.md:1`, `skills/new-product/SKILL.md:1`, `skills/improve/SKILL.md:1`,
  `skills/ask/SKILL.md:1`, `skills/design/SKILL.md:1`, `skills/test/SKILL.md:1` — корни оркестраторов: frontmatter (`name`,
  `description`) + тело (ментальная модель, таблица под-агентов, start/resume, operating rules, телеметрия).
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
- `skills/design/SKILL.md:1`, `skills/design/phases.md`, `skills/design/dashboard-guide.md`,
  `skills/design/feedback-loop.md`, `skills/design/state-schema.md` — reference-файлы `/design`. Доменный —
  `phases.md` (машина `INTAKE→AUDIT→COMPOSE→CONSENT GATE→IMPLEMENT→VERIFY→DONE`, список 7 призм, формат
  **единого аннотированного демо** «Вариант А»). Переносимые: `dashboard-guide.md` (модель `demo`-блока с
  **одним** аннотированным мокапом + контракт **CONSENT GATE** per-finding `f<k>`, дефолт «Применить»),
  `feedback-loop.md` (старт сервера + батч на гейте), `state-schema.md` (+design-поля). Список «читай по
  надобности» — `skills/design/SKILL.md:58`.
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
- `agents/ds-auditor.md:1`, `agents/ds-coder.md:1` — ростер `ds-*` для `/design` (**оба без `model:`** —
  дефолт сессии, как `wf-*`). `ds-auditor` — read-only UI/UX-критик (`tools: Read, Grep, Glob, Bash` — без
  Write/Edit, структурная гарантия read-only): аудитит компонент сквозь **одну** призму, читает `INDEX.md`
  первым, возвращает **структурированные находки** `{ id, prism, severity, problem, location, proposal }`
  (ключи и enum `severity` — английские/точные, оркестратор парсит детерминированно; проза `problem`/
  `proposal` — на языке вывода). **Не рисует демо и никого не спавнит.** `ds-coder` — исполнитель
  (`tools: Read, Write, Edit, Bash, Grep, Glob`): применяет **одну одобренную** находку из готового плана
  (находка = план), независимые идут параллельно. **Два отдельных файла из-за разного tool-set**
  (read-only auditor vs Write coder), а не из-за модели — реюз `wf-explorer`/`wf-coder` невозможен (их
  контракты под EXPLORE/IMPLEMENT, плюс модель глобальна для `subagent_type`; ADR-0006). `wf-reviewer`
  `/design` реюзит в VERIFY. Подробнее — ADR-0023.
- `.claude-plugin/plugin.json:1` — манифест плагина: **только метаданные**, никакого перечисления
  скиллов/агентов/команд.
- `README.md:39` — раздел про `/new-product`; раздел про `/improve` (рой/консенсус/выбор/fan-out);
  раздел про `/ask` (read-only Q&A → визуальный ответ → чат); раздел про `/design` (фокусный UI/UX-аудит
  одного компонента → единый аннотированный демо → реализация); раздел «Layout» (канонические каталоги,
  включая `skills/improve/`, `skills/ask/`, `skills/design/`, `wf-improver`, `ask-researcher`, `ds-*`).

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
- **Инструкции — английские; язык вывода человеку = язык запроса (побеждает).** INTAKE-шаг скилла
  авто-детектит язык запроса человека → пишет его в `state.json.lang` → передаёт под-агентам в промпте
  спавна. Глобальная настройка (`~/.claude/ai-pathfinder/settings.json`, `en` при отсутствии — eng-first)
  — **фолбэк**, когда запроса нет (autonomous/eval), и язык хрома UI. `lang` управляет ВСЕМ
  human-facing выводом (нарратив, артефакты, дашборд, чат/реплаи). **Всегда английские** (если человек
  явно не попросил иначе): `docs/knowledge/**`, README, git-коммиты. **Машинно-парсимые заголовки/ключи**
  (секции `ask-researcher`, `cand:`-ключи `wf-improver`, схемы дайджестов) остаются **английскими**
  всегда — иначе ломается парсер оркестратора. Эволюция: жёсткое «human-facing → Russian» → eng-first +
  чат-исключение (ADR-0018) → «язык запроса побеждает» (ADR-0022). Подробно — `areas/dashboard-i18n.md`
  §«Язык агента».

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

## Карта `/design`: рой призм → единый аннотированный демо → гейт согласия → реализация

Пятая команда (`skills/design/SKILL.md:1`) — **фокусный UI/UX-аудит ОДНОГО компонента**. В отличие от
`/improve` (бэклог по всему приложению) и `/ask` (read-only объяснение), `/design` берёт **один** элемент,
критикует его широко (много призм), но **узко по скоупу**, и **сам реализует** одобренные правки через свой
ростер `ds-*`. Стадии (`state.json.phase`, `skills/design/SKILL.md:40`):

```
INTAKE → AUDIT → COMPOSE → CONSENT GATE → IMPLEMENT → VERIFY → DONE
```

- **INTAKE — вход имя и/или скриншот.** Компонент задаётся **именем и/или скриншотом**: имя → оркестратор
  находит компонент в коде (Grep/Glob); скриншот → человек прикрепляет изображение на дашборде (контракт
  вложений ADR-0020), оркестратор `Read`'ит сохранённый файл и передаёт визуальный контекст аудиторам.
  Только-имя → от кода; только-скриншот → от визуала + поиск парного кода; оба → перекрёстная сверка.
- **AUDIT — рой по 7 призмам.** Оркестратор спавнит `ds-auditor` параллельно — **по одному на призму** (или
  группируя при перекрытии): (1) визуальная иерархия и эстетика; (2) интеракция, фидбэк и аффордансы;
  (3) движение/микро-анимация; (4) раскладка и адаптивность; (5) копирайт/ясность; (6) доступность (a11y);
  (7) логика потока / информационная архитектура. Каждый читает `INDEX.md` первым, аудитит **только** свою
  призму и отдаёт находки `{ id, prism, severity, problem, location, proposal }`. Затем **оркестратор
  консолидирует и дедуплицирует** их в один ранжированный список — аудиторы не консолидируют и никого не
  спавнят (инвариант ADR-0006).
- **COMPOSE — ОДИН аннотированный демо.** Оркестратор (у него Write) строит **единый** самодостаточный
  `mockups/redesign.html`, покрывающий **все** находки в стиле **«Вариант А»**: нумерованные бейджи ①②③ +
  боковая легенда (номер → проблема → что изменилось) + тогл **«До/После»**. Инлайн-CSS/JS, без CDN/сети
  (под CSP). `dashboard.json.demo` — **единственный** вариант (`selectionId:"design-demo"`,
  `variants:[{id:"redesign",…}]`), а **не** pick-one набор: человек смотрит цельную картину правок, не
  выбирает «один дизайн». Подаётся штатным `demo`-механизмом (`GET /mockup` в sandbox-iframe) — **0 правок
  сервера**. Почему один демо, а не N — **ADR-0023**.
- **CONSENT GATE — per-finding `f<k>`, дефолт «Применить» (opt-out).** Каждая находка = карточка
  `planBlocks[].id = f<k>` + вопрос `questions[kind:"choice"].id = f<k>`, `options:["Применить",
  "Пропустить"]`. **Дефолт — «Применить»**: нет ответа = находка применяется (намеренная **инверсия**
  opt-in выбора фич в `/improve` — человек уже сам указал компонент). Человек снимает галки с ненужных →
  **«Отправить»** (Submit) → **«Утвердить план»** (`approve-plan` = «реализуй оставшийся набор»). Порядок
  Submit→Approve обязателен (`draft.json` не READABLE). **0 правок сервера/HTML** — реюз контракта
  `feat-K` (`questions[choice]`+`approve-plan`, ADR-0013), отличается только дефолт. Подробнее — **ADR-0023**.
- **IMPLEMENT — только одобренное.** На каждую находку «Применить» (или сгруппированный work-stream
  связанных) оркестратор спавнит `ds-coder` с Write/Edit; план = сама находка, кодеры просто применяют;
  независимые идут параллельно.
- **VERIFY — только `/code-review`.** В отличие от `/feature` (оба ревью-гейта), `/design` гоняет
  `wf-reviewer` (реюз из feature) + **только** `/code-review` (без `/security-review` — фокусные UI/UX-правки
  компонента редко несут поверхность безопасности; человек запустит вручную при нужде).
- **DONE.** Узкий скоуп → база знаний растится **лайтово** самим оркестратором (строка в `task-log.md`),
  без полного прохода `wf-documenter`.

**`/design` поверх существующего контракта.** Находки = `planBlocks`/`questions[choice]`, демо =
`demo`/`mockups`, гейт = `approve-plan`, попадание в хаб — автоматически. **0 правок `server.py`** — пятый
потребитель ride-the-contract (после `/feature`-фидбэка, `/new-product`-гейтов, `/improve`-гейта, `/ask`);
lineage ADR-0008/0013/0016. Ростер `ds-*` без пина модели — **ADR-0023** (lineage ADR-0006).

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

_updated: 2026-06-25 (design-command: пятая команда `/design` — фокусный UI/UX-аудит одного компонента, рой `ds-auditor` по 7 призмам → единый аннотированный демо «Вариант А» → гейт согласия per-finding `f<k>` дефолт «Применить» → `ds-coder`; новый ростер `ds-*` без пина модели, 0 правок сервера, ADR-0023. Предыдущее — request-language-wins: язык вывода = язык запроса человека (ADR-0022). Ранее — feature-fast-lane: гейт TRIAGE §0, Fast Lane, `state.json.lane`)_
