# Область: Ввод обратной связи в дашборде (вкладка «Контент»)

> Подсистема, через которую человек оставляет агенту правки на вкладке «Контент»: комментарии к
> блокам/прозе, ответы на вопросы (`open`/`choice` + **свой свободный ответ**), выбор и **комментарии к
> демо-вариантам**. Целиком фронтенд `templates/dashboard.html`; сервер агностичен к содержимому items.

## Назначение

Собрать в очередь черновика (draft) разнородные сигналы человека и отправить их агенту батчем
(`/submit`), а реплаи агента (`replies.json`) показать инлайн под соответствующим якорем. Задача
`dashboard-feedback-enhancements` добавила сюда две возможности, **не трогая `scripts/server.py`**:
свой свободный ответ на `choice`-вопрос и явное (всегда видимое) поле комментария у каждого
демо-варианта.

## Ключевые файлы (`templates/dashboard.html`)

- `:627` — `render(data, replies)`: рисует карточки «Сводка / Карта / Демо / План / Вопросы /
  Work-streams». Строит `repliesByBlock` (по `r.blockId`) и `repliesByQ` (по `r.questionId`) из
  `replies.replies` (`:638`). Обрамляет переустановку `#content.innerHTML` (`:845`) парой
  `captureActiveInput()` (`:844`, **до** innerHTML) → `restoreActiveInput(__snap)` (`:847`, **после**
  `wireBlocks()`), чтобы тик автополлинга не сбросил активный ввод. В конце зовёт
  `wireBlocks()`/`highlightComments()`/`updateQueue()`.
- `:670` — ветка `item.kind==="choice"`: radio-опции **плюс** всегда видимый блок `.own-answer` с
  `<textarea data-answer="<item.id>">` (`:677`). Предзаполнение «своего ответа» (`:672`): берётся
  существующий `answer` по `questionId`, если его `text` **не совпадает ни с одной `options`** —
  тогда radio без `checked`, текст в поле; иначе `checked` на опции, поле пустое.
- `:679` — ветка `open`: одиночный `<textarea data-answer>` (тот же контракт).
- `:705` — `region(anchor, innerHtml, repliesByBlock)`: прозо-регион с select-to-comment (summary /
  codebaseMap / caption варианта). Внутри зовёт `regionFooter`.
- `:713` — `regionFooter(anchor, repliesByBlock)`: реплаи агента (`repliesByBlock[anchor]`) + карточки
  оставленных комментариев (`draftItems` по `blockId===anchor`). **Единственный источник** рендера
  реплаев/коммент-карточек на якорь.
- `:719` — `cmtCard(d)`: карточка одного черновикового комментария (с цитатой `selectedText`, если есть).
- `:724` — `renderDemo(demo, repliesByBlock)`: выбор варианта = `answer` по `selectionId` (`:726`);
  на **каждый** `vr` рендерит блок `.v-comment` с `<textarea data-comment-variant="<vr.id>">` +
  кнопкой «Отправить» (`:734`). `regionFooter(vr.id,…)` зовётся **один раз** в `.v-body` (`:746`) —
  в т.ч. для варианта без `caption`. `caption` (если есть) остаётся select-to-commentable, но **без**
  собственного футера (`:733`) — чтобы реплаи/карточки не дублировались.
- `:755` — `wireBlocks()`: навешивает обработчики (см. «Взаимное исключение»).
- `:939` — `captureActiveInput()` / `:949` — `restoreActiveInput(snap)`: сохранение/восстановление
  **активного незабленённого** textarea вокруг перерисовки (задача `preserve-dashboard-input-on-poll`,
  чистый фронтенд). `captureActiveInput` снимает снапшот **только** сфокусированного textarea внутри
  `#content` с ключом `data-answer` **или** `data-comment-variant` (value, `selectionStart/End`, факт
  фокуса); иначе `null`. `restoreActiveInput` ищет узел по тому же стабильному data-ключу (через
  `cssesc`), переписывает прованным значением предзаполнение шаблона, возвращает фокус и каретку
  (с клампом по длине). Тихий выход, если поле исчезло (вопрос/вариант удалён агентом на лету) или
  фокус был не в `#content`. Образец — save/restore скролла из `renderChat`.
- `:798` — `saveAnswer(qid, val)`: `POST /draft {kind:"answer", questionId, text}` → `loadDraft()`.
- `:891` — `sendComment()`: select-to-comment — `POST /draft {kind:"comment", blockId, selectedText, text}`.
- `:898` — `saveVariantComment(variantId, value, ta)`: тонкая обёртка над тем же контрактом, что
  `sendComment`, но `blockId=variantId`, `selectedText:""`; **очищает поле** (`:901`) перед запросом,
  чтобы на один вариант можно было оставить несколько комментариев.

## Сигнал awaiting (задача ждёт ответа человека)

Когда `data.status === "awaiting-batch"` (агент дошёл до батч-гейта), дашборд **фоном сигналит**
человеку, даже если вкладка свёрнута. Чистый фронтенд (`templates/dashboard.html`, задача
`awaiting-human-signal`), серверные контракты не тронуты.

- **Детект перехода `working → awaiting`** (`:790`): модуль-переменная `prevStatus` (`:528`) хранит
  прошлый `data.status`; `enter = prevStatus !== null && prevStatus !== "awaiting-batch" && data.status
  === "awaiting-batch"`. Сразу после — `prevStatus = data.status`. **Первый `render()` задаёт базовый
  статус без сигнала** (`prevStatus` стартует `null`) — анти-спам: открыл вкладку, а задача уже на гейте
  → уведомления нет, сигналим **только по фронту** перехода.
- **Браузер-`Notification` при переходе** (`:795`): только если `enter && document.hidden && "Notification"
  in window && Notification.permission === "granted"` — то есть лишь когда вкладка реально в фоне и
  разрешение уже выдано. Всё в `try/catch`, **грейсфул**: нет API/разрешения → молча ничего.
- **Title-бейдж** (`setTitleBadge`, `:534`): при awaiting `document.title` → `"● ждёт — <title>"`, иначе
  возврат к `baseTitle` (статичный заголовок, снят при старте, `:529`). Виден в фоновой вкладке без
  Notification-разрешения — это базовый, всегда-работающий канал.
- **`visibilitychange`** (`:2343`): возврат на вкладку (`!document.hidden`) снимает title-бейдж
  (`setTitleBadge(false)`) — человек уже смотрит, бейдж не нужен.
- **`requestPermission` на жесте** (`:1101`/`:1107`): разрешение на уведомления запрашивается **только**
  из обработчика клика (approve/submit), не на старте — браузеры душат `requestPermission` вне
  пользовательского жеста.
- **Контекстная подсветка actionbar** (`:803`): `#actionbar` получает класс `.awaiting` при awaiting →
  `.actionbar.awaiting` (`:163`) красит фон/верхнюю границу из `--warn-soft`/`--warn` (токены есть в
  **обеих** темах, ADR-0015).

> **Именная шероховатость токена.** Подсветка дашборда берёт `--warn-soft`/`--warn`, а аналогичная
> подсветка карточки в хабе — `--awaiting-soft`/`--warn` (`scripts/server.py`). **Значения одинаковые**
> (`#fff7ed` светлая / `#2a2113` тёмная), имена разные — зафиксировано в ADR-0015 как осознанная
> шероховатость, не баг.

## Модель draft (что копится во фронте → уходит в submission)

`draftItems` — массив items, синхронный с `draft.json` (через `loadDraft()` `:807`). Два вида:

- **`answer`** — `{kind:"answer", questionId, text}`. Один на `questionId` (серверный дедуп, см. ниже).
  Несёт ответ на `open`/`choice`-вопрос **или** выбор демо-варианта (`questionId = demo.selectionId`).
  Для `choice`: `text` может **не совпадать ни с одной `options`** — это и есть свободный ответ.
- **`comment`** — `{kind:"comment", blockId, selectedText, text}`. `blockId` — якорь региона;
  `selectedText` пустой у явного коммента к варианту, непустой у select-to-comment.

## Якоря (`blockId` / `questionId`)

- `questionId` — id вопроса (`questions[].id`) **или** `demo.selectionId` (выбор варианта — тоже answer).
- `blockId` ∈ `summary` | `codebaseMap` | id блока плана (`b1`…) | `variants[].id` (`v1`/`v2`/…).
  Якоря стабильны между итерациями — реплай агента ищется по совпадению `blockId`/`questionId`.

## Контракт с сервером (без правок `server.py`)

- `POST /draft` → `_draft_add` (`scripts/server.py:714`): мерджит item в `draft.json`. Набор полей item
  **жёстко фиксирован** (`:720`) — лишние поля молча отбрасываются, поэтому обе фичи влезают только в
  существующие поля (`questionId`/`blockId`/`selectedText`/`text`). Для `kind:"answer"` с непустым
  `questionId` — **дедуп**: новый answer заменяет прежний того же вопроса (`scripts/server.py:730`).
- `POST /submit` → `_submit` (`scripts/server.py:747`): пишет весь draft в `submissions/<n>.json`,
  чистит draft, шлёт signal `submit`, будит `/wait`. Агент читает submission и пишет `replies.json`.
- `GET /replies` (`scripts/server.py:223`): отдаётся как есть; рендерится через `regionFooter`/реплаи
  по вопросу.

## Инварианты

- **Свой ответ перебивает выбор опции, и наоборот** (`wireBlocks` `:760`/`:766`): ввод в поле
  своего ответа снимает radio (`oninput`), выбор radio чистит поле своего ответа. Оба пишут **один**
  `answer` того же `questionId` — взаимное исключение и на клиенте, и сервером (дедуп).
- **Сохранение по `onchange` (blur) / Cmd+Enter**, не по каждому символу — иначе polling-перерисовка
  (`render` зовётся из `loadDraft`/`rerender`) затёрла бы недосохранённый ввод. У коммента к варианту —
  кнопка «Отправить» или `Cmd/Ctrl+Enter` (`:780`).
- **`regionFooter(vr.id)` рендерится ровно один раз на вариант** (`:746`), в т.ч. без `caption`. Это и
  чинило исходный баг: реплаи агента по `blockId===vr.id` раньше были видны только при наличии caption.
- **Поле коммента у варианта видно всегда** (`:734`), независимо от `caption`.
- **Активный ввод переживает перерисовку** (`captureActiveInput`/`restoreActiveInput` вокруг innerHTML в
  `render()`): незабленённый текст ответа на вопрос или коммента к демо-варианту + позиция каретки не
  теряются на тике автополлинга. Спасается **только активное (сфокусированное) поле** (q2=A) — по одному
  на перерисовку; остальные поля и так перечитываются из draft. Skip-render не вводился: реальные апдейты
  плана от агента доходят сразу. Не отменяет инвариант «сохранение по blur/Cmd+Enter» — он про долговечную
  запись в draft, а capture/restore лишь латает окно «ввод не забленён, а DOM уже пересобран».
- **Сигнал awaiting грейсфул и анти-спамный.** Уведомление шлётся **только по фронту** перехода
  `working→awaiting` (`prevStatus`, первый render — базовый без сигнала) и **только** когда вкладка в
  фоне с выданным разрешением; без Notification/разрешения остаётся title-бейдж и подсветка actionbar.
  Возврат на вкладку снимает бейдж (`visibilitychange`). Серверный контракт `data.status` не менялся.
- Совместимость аддитивна: вопрос без `options` рендерится как `open`; задача без `demo` — как раньше.

## Подводные камни

- `_draft_add` отбрасывает неизвестные поля — нельзя протащить «id варианта» отдельным полем; он кладётся
  в существующий `blockId` (решение ADR-0008). Не вводить новые поля без правки сервера.
- `saveVariantComment` чистит textarea **до** await (`:901`) — намеренно, чтобы быстрый повторный ввод
  начинался с чистого поля; повторный рендер из `loadDraft` всё равно вернёт пустое поле.
- `applyHighlights`/`wrapRange` (`:829`/`:843`) подсвечивают только комменты с непустым `selectedText`;
  у явного коммента к варианту `selectedText:""` → `mark.commented` не рисуется (это ок by design).
- Свой ответ без совпадения с опцией требует, чтобы агент читал `answer.text` как свободный текст —
  это контракт скилла (`skills/feature/dashboard-guide.md`, `feedback-loop.md`), а не сервера.

## Как расширять

- **Новый вид региона с комментарием:** дать ему стабильный `data-anchor=<id>` и вызвать
  `regionFooter(id, repliesByBlock)` — реплаи/карточки подтянутся автоматически; коммент слать
  `kind:"comment"` с этим `blockId` (как `saveVariantComment`).
- **Новый kind ответа:** реюзать `saveAnswer`/`[data-answer]` — обработчик навешивается на **все**
  `textarea[data-answer]` (`:757`), поэтому новый виджет с этим атрибутом подхватится сам.
- **Любое новое поле обратной связи** должно либо влезть в фиксированный item (`_draft_add` `:720`),
  либо потребует +1 строки в сервере — см. `areas/dashboard-changes-tab.md`-стиль решения (дерево/схема
  на фронте, бэкенд почти нетронут).

_updated: 2026-06-16 (awaiting-human-signal: фоновый сигнал awaiting — Notification/title/actionbar)_
