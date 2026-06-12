# Область: Скиллы-оркестраторы и ростеры под-агентов

> Как в этом плагине устроены команды-оркестраторы (`/feature`, `/new-product`) и их под-агенты:
> регистрация конвенцией каталогов, паттерн «SKILL.md + reference-файлы», frontmatter агентов
> (включая `model:`) и почему ростеры `wf-*` и `np-*` раздельны. Эта область раньше отсутствовала в
> базе знаний — следующий агент не должен заново выводить устройство оркестраторов из кода
> (флажок из `.workflow/tasks/new-product-workflow/exploration.md:160`).

## Назначение

Плагин ai-pathfinder реализует **slash-команды как скиллы-оркестраторы**. Главный агент (оркестратор)
не пишет код сам — он исполняет машину стадий и спавнит специализированных под-агентов через Agent tool.
Две команды:

- **`/feature`** — работа в **существующей** кодовой базе: EXPLORE кода → план → один проход IMPLEMENT
  (`skills/feature/SKILL.md:14`).
- **`/new-product`** — **greenfield** с нуля: DISCOVER (элиситация + ресёрч) → PRD → план фаз →
  эволюционный build-loop (`skills/new-product/SKILL.md:15`). Сиблинг `/feature`; отличие — стартовая
  точка (пустой репозиторий vs существующий код).

## Ключевые файлы

- `skills/feature/SKILL.md:1`, `skills/new-product/SKILL.md:1` — корни оркестраторов: frontmatter
  (`name`, `description`) + тело (ментальная модель, таблица под-агентов, start/resume, operating rules,
  телеметрия).
- `skills/new-product/phases.md`, `skills/new-product/loop.md`,
  `skills/new-product/feedback-loop.md`, `skills/new-product/dashboard-guide.md`,
  `skills/new-product/state-schema.md`, `skills/new-product/knowledge-guide.md` — reference-файлы
  оркестратора `/new-product` (детали стадий, цикла, сервера, рендера, state, базы знаний). Список и
  семантика «читай по мере надобности» — `skills/new-product/SKILL.md:47`. Зеркало feature-набора в
  `skills/feature/`.
- `agents/np-thinker.md:1`, `agents/np-researcher.md:1`, `agents/np-coder.md`, `agents/np-judge.md` —
  ростер `np-*` для `/new-product` (с полем `model:`).
- `agents/wf-explorer.md:1`, `agents/wf-planner.md:1`, `agents/wf-coder.md:1`,
  `agents/wf-reviewer.md:1`, `agents/wf-documenter.md:1` — ростер `wf-*` для `/feature` (без `model:`).
  `wf-reviewer`/`wf-documenter` переиспользуются `/new-product` как есть.
- `.claude-plugin/plugin.json:1` — манифест плагина: **только метаданные**, никакого перечисления
  скиллов/агентов/команд.
- `README.md:39` — раздел про `/new-product`; `README.md:42` — раздел «Layout» (канонические каталоги).

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
**переустановки/refresh** плагина (`/plugin install ai-pathfinder@algolguru`), а не «на лету» в уже
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

Файл `agents/<name>.md` — frontmatter + английское тело (роль/процедура/выход; артефакты пишутся
по-русски). Поля frontmatter:

- **`name`** = `subagent_type`, по нему оркестратор спавнит агента (`agents/wf-coder.md:2`,
  `agents/np-thinker.md:2`). Глобален в плагине — двух агентов с одинаковым `name` быть не может.
- **`description`** — когда применять (драйвит выбор/триггер агента).
- **`tools`** — список разрешённых инструментов через запятую (`agents/wf-coder.md:4`,
  `agents/np-thinker.md:5`). Набор инструментов — это **структурная гарантия роли**: напр. у
  `np-thinker` стоит ровно `Read, Write, Edit` (без Grep/Glob/Bash/Web) — так «мыслитель физически не
  читает сырьё» (`agents/np-thinker.md:5`, `agents/np-thinker.md:14`); у read-only ролей
  (`wf-reviewer`, `np-judge`) нет Write/Edit.
- **`model:`** — модель под-агента (**только у ростера `np-*`**: `agents/np-thinker.md:4` = `fable`,
  `agents/np-researcher.md:4` = `opus`). У ростера `wf-*` поля нет — `/feature` идёт на дефолтной
  модели сессии. Значения — **алиасы** (`fable`/`opus`), не полные id.

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
  выбрать не тот скилл.
- **Регистрация только при (пере)установке** — не «на лету» в идущей сессии.
- **Инструкции — английские, артефакты/дашборд/база знаний/тексты человеку — русские**
  (`skills/feature/SKILL.md:81`, `skills/new-product/SKILL.md:113`).

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
  сознательное решение «без правок сервера/HTML» (см. ADR-0007).

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
  **только если** его модель и инструменты подходят как есть (так `/new-product` реюзит
  `wf-reviewer`/`wf-documenter`).

_updated: 2026-06-12_
