# Область: Визард code-review («Ревью») дашборда

> Пошаговый визард ревью собственного диффа `/feature`: под-фаза **REVIEW** между VERIFY и DONE.
> Отдельная (6-я) вкладка «Ревью», модель в `dashboard.json.review`, тела ханков — из `/changes?file=`,
> комментарии человека — по существующему каналу anchored-тредов, закрытие — сигнал `approve-plan`.
> **0 правок `server.py`** — ride-the-contract (ADR-0027).

## Назначение

После зелёного VERIFY (и прогона гейтов `/code-review` + `/security-review`, §6 `phases.md`) агент
становится ревьюером **собственного диффа**: берёт `git diff <state.baseCommit>` в worktree, **ранжирует
файлы по важности** и внутри каждого — **ханки/блоки по важности**, приписывает к каждому «что/зачем» и
ведёт человека через степпер. Человек комментирует файл/ханк (anchored-тред), агент правит код и отвечает
**тем же якорем** — цикл до отсутствия открытых комментариев; человек завершает кнопкой «Завершить ревью»
(сигнал `approve-plan`). Вкладка «Изменения» показывает **плоское** дерево диффа без важности/аннотаций;
«Ревью» добавляет ранжирование, аннотации и тред-цикл поверх того же диффа.

## Модель (`dashboard.json.review`)

```jsonc
"review": {
  "summary": "markdown — что за фича, размер диффа, на что смотреть первым",
  "status": "open",            // "open" | "resolved" (→ "resolved" на approve-plan)
  "iteration": 1,               // бампается каждый круг правок
  "steps": [                    // ФАЙЛЫ, порядок массива = ранжирование (rank 1 первым)
    { "file": "scripts/server.py", "anchor": "rev:scripts/server.py",
      "status": "modified", "added": 42, "removed": 5,
      "rank": 1, "kind": "logic",          // "logic" | "cosmetic"
      "comment": "markdown — что/зачем по файлу",
      "blocks": [                          // ХАНКИ, порядок = ранжирование
        { "anchor": "rev:scripts/server.py#0",  // "<fileAnchor>#<hunkIdx>", стабилен по ходу тиков
          "hunkHeader": "@@ -120,7 +120,9 @@ def do_GET",
          "range": [120,128], "rank": 1, "kind": "logic",
          "comment": "markdown — что/зачем по ханку" } ] } ]
}
```

- **Якорь по индексу ханка** (`rev:<path>#<idx>`), **не** по диапазону строк — диапазоны плывут между
  итерациями правок, индекс стабилен, тред остаётся привязан к правильному ханку.
- **Тела ханков НЕ дублируются** в модель — визард тянет полный дифф файла из `GET /changes?file=<path>`
  (тот же, что показывает вкладка «Изменения») и вырезает ханк по `@@`-заголовку на клиенте (`hunkSlice`).
- **Эвристика важности:** публичный контракт/API + объём реальной логики + риск (security/auth,
  запись/персистенс, парсинг) → выше; переименования/переформат/проброс параметра/фикстуры → `cosmetic`.
- Схема описана в `skills/feature/phases.md` §6.5 и `skills/feature/dashboard-guide.md`; `review` —
  **feature-specific** поле, `skills/_shared/dashboard-contract.md` его НЕ описывает.

## Ключевые файлы

### Фронтенд (`templates/dashboard.html`)

- `:791` / `:826` — 6-я вкладка `#tab-review` (`role="tab" aria-controls="review"`) и панель `#review`
  (`role="tabpanel"`, `.wrap wide`, `hidden` по умолчанию).
- `:2942` — в `switchTab`: `reviewTick()` + запуск `reviewTimer` (`setInterval 5000`); при уходе таймер
  гасится. `#review.hidden` переключается `:2925`. Таймер объявлен `:2914`.
- `:2511` — в чат-тике: `if(activeTab === "review") renderReview()` — счётчик «ждут ответа» и реплаи
  остаются живыми при апдейте чата.
- `:3394` — `reviewTick()`: тянет `lastData.review`, клампит `reviewCursor`, один раз на смену файла
  фетчит `/changes?file=` в `selectedReviewDiff`, зовёт `renderReview()`.
- `:3417` — `hunkSlice(diffText, hunkHeader)`: вырезает один ханк из полного unified-diff (от совпавшего
  `@@` до следующего `@@`); при отсутствии заголовка — весь дифф.
- `:3441` — `reviewRankChip(n)`: простой порядковый чип важности (1..N), **не** структурный `rankChip()`
  improve-гейта. `:3446` — `kindChip(kind)`: label logic/cosmetic. `:3451` — `reviewDelta(step)`: `+/−`.
- `:3456` — `gotoStep(i)`: клампит курсор, сбрасывает `selectedReviewFile` (форс ре-фетч диффа),
  объявляет шаг в `#phase-announce` (a11y), зовёт `reviewTick()`.
- `:3470` — `renderReviewRail()`: рельс файлов (ранг-чип, путь, kind, `+/−`, статус waiting/handled,
  список блоков); `:3495` — `renderReviewStep()`: hero + аннотация файла + блоки (ханк-дифф через
  `hunkSlice`+`renderDiff` + `regionFooter` тред на блок) + тред файла; `:3519` — `renderReviewStepper()`:
  прогресс + «Назад/Далее».
- `:3531` — `renderReview()`: sig-гард (`iteration#cursor#file#diffLen#collapsed#openThreads#confirm`) →
  DOM no-op при отсутствии изменений; собирает `.rv-wrap` (rail + stage + foot с «Завершить ревью»);
  считает открытые `rev:*`-треды в «N ждут ответа». `:3604` — `wireReview()` (реюзит `wireBlocks`).
- `:3589` / `:3595` — свои `captureReviewInput`/`restoreReviewInput`, **scoped к `#review`** (штатные
  `captureActiveInput` scoped к `#content` и завязаны на `render()`), чтобы фокус в textarea треда
  пережил ре-рендер визарда.
- `:3624` — `reviewDone()`: если есть открытые `rev:*`-треды → инлайн-подтверждение (`reviewConfirmOpen`);
  `:3633` — `finishReview()`: `flushDraft()` + `doApprove()` (реюз plan-gate approve, ADR-0026).
- `:1162`–`:1184` (en) и `:1450`+ (ru) — 25 ключей `STR` `tab.review`/`review.*` в **обоих** словарях;
  плюс `review.running/done/failed` для лейблов run-статуса (`:1305`).

### Скилл (`skills/feature/`)

- `phases.md` §6.5 «REVIEW (the code-review wizard)» (`:198`) — как взять дифф, ранжировать, писать
  `review`, парковаться; схема модели; autonomous/eval-ветка (не парковаться, сразу DONE).
- `feedback-loop.md` — «Review wizard cycle» (чтение тредов, правка, ответ тем же якорем, ре-ранжирование).
- `dashboard-guide.md` — поле `review` в контракте дашборда.
- `state-schema.md:40` — фаза `REVIEW` в цепочке `… → VERIFY → REVIEW → DONE`, **нетерминальна**, активна
  в хабе (как `ANSWER` у `/ask`).

## Публичный интерфейс (реюз, без новых эндпоинтов)

- `GET /data` — `review` едет как новое агностичное поле (`lastData.review`).
- `GET /changes?file=<path>` — тела ханков (тот же дифф, что вкладка «Изменения»).
- `POST /chat` c `anchor` (verbatim `rev:<path>` / `rev:<path>#<idx>`) — комментарий человека; агент
  отвечает тем же якорем.
- `POST /signal { signal:"approve-plan" }` — «Завершить ревью» (через `flushDraft`+`doApprove`).

## Инварианты

- **0 правок `server.py`** — весь визард поверх существующего контракта. `review` — feature-specific поле;
  `_shared/dashboard-contract.md` не трогается.
- **Якорь по индексу ханка**, не по диапазону строк — стабильность треда между итерациями.
- **Тела ханков не дублируются** в `review` — единственный источник диффа `/changes?file=`.
- **Порядок массивов `steps`/`blocks` = ранжирование** (rank 1 первым).
- **Sig-гард `renderReview`** несёт `reviewCursor` + collapse + счётчик открытых тредов → 5-с поллинг
  не теряет место человека и состояние тредов. Не убирать курсор из сигнатуры.
- **Свои `capture/restoreReviewInput`** scoped к `#review` (штатные — к `#content`); фокус textarea треда
  переживает ре-рендер.
- **REVIEW нетерминальна** — задача остаётся активной в хабе, пока визард жив (иначе History спрячет её).
- **Инвариант паритета** `index.html` ↔ `templates/dashboard.html` (ADR-0024) — весь FE в шаблоне.
- **Машинные ключи английские** (`tab.review`/`review.*`), локализуются лишь значения; паритет словарей
  en/ru покрыт smoke-тестом.

## Подводные камни

- **`hunkSlice` матчит по `@@…@@`-префиксу**, толерантен к отличающемуся хвостовому контекст-лейблу; при
  ненайденном заголовке отдаёт весь дифф файла (fallback, не пустоту).
- **Диапазоны `range:[start,end]` — справочные**, привязка треда идёт по `anchor`/индексу, не по range.
- **Курсор клампится** при усадке `steps` между итерациями (`reviewTick`/`gotoStep`) — иначе после
  ре-ранжирования визард смотрел бы в несуществующий шаг.
- **Autonomous/eval:** структура публикуется, но **без парковки** — сразу DONE (аналогично пропуску
  парковки на PLAN GATE); ранжированные шаги перечисляются в DONE-нарративе.

## Как расширять

- **Новое поле шага/блока:** дополнить запись, которую пишет оркестратор (`phases.md` §6.5) и потребление
  в `renderReviewRail`/`renderReviewStep`; помнить про sig-гард `renderReview`.
- **Другая эвристика важности:** это **эвристика оркестратора** (§6.5), FE лишь рендерит `rank`/`kind`.
- **Новый STR-ключ:** добавлять в **оба** словаря (en/ru), иначе падает smoke-тест паритета.

_updated: 2026-07-05 (code-review-wizard: новая под-фаза REVIEW + вкладка «Ревью», модель
`dashboard.json.review`, тела ханков из `/changes?file=`, комменты по anchored-тредам, закрытие
`approve-plan`; 0 правок сервера; ADR-0027, пин `tests/test_review_wizard.py`)._
