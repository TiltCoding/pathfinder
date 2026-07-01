# Дизайн: «Утвердить план» вбирает ответы (без принудительного круга доработки)

_Дата: 2026-07-01 · ветка: `worktree-plan-gate-approve-absorbs-answers`_

## Проблема

На гейте плана дашборда кнопка **«Утвердить план»** заблокирована, пока в черновике
есть неотправленные элементы:

```js
// templates/dashboard.html — updateApproveGate()
const blocked = draftItems.length > 0 || (improveGate && !submittedOnce);
```

Но каждый ответ на вопрос / выбор варианта сохраняется в черновик как `draft`-элемент
(`kind:"answer"`, `saveAnswer → POST /draft`). Единственная кнопка, отправляющая черновик, —
**«Отправить агенту на доработку»** (`POST /submit`). После неё оркестратор по протоколу
делает полный revision-цикл (применяет ответы → пишет реплаи → перерисовывает дашборд
обратно в `awaiting-batch`).

Итог: чтобы «выбрал варианты → в реализацию», человек вынужден пройти
`Submit → ждать ревизию → Approve`. Ответы (входные данные плана) трактуются как «доработка».

**Каналы (важно для дизайна):**
- Ответы на вопросы / выбор вариантов → `/draft` (`kind:"answer"`) → копятся → блокируют Approve.
- Свободные комментарии (выделить-и-прокомментировать, коммент к варианту, реплай в треде)
  → `postAnchored → /chat` → отправляются агенту **сразу**, в черновик не попадают, Approve не блокируют.

## Цель

Выбрал варианты / ответил на вопросы → нажал **«Утвердить план»** → агент **сразу**
идёт в реализацию, без принудительного круга доработки. Работает на **обоих** гейтах:
план `/feature` и выбор фич `/improve`.

## Дизайн

### Frontend — `templates/dashboard.html`

**`updateApproveGate()`**: «Утвердить план» больше не `disabled` из-за `draftItems.length`.
Кнопка активна на гейте; вся логика — в обработчике клика. (Убираем зависимость от
`submittedOnce`.)

**Общий хелпер `flushDraft()`** — выносим из `#btn-submit.onclick` отправку черновика:
снимок `markedFeatures()` (для `dispatchPreview`), `POST /submit`, очистка `draftItems`.
Возвращает успех/`picks`. Реюзают и Submit, и Approve.

**Классификация черновика (для «спрашивать»):**
- `hasFreeformAnswer` = есть `answer`, чей `text` не совпадает ни с одной из `options`
  своего вопроса (человек написал правку, а не выбрал вариант).
- `hasOpenThread` = есть якорный тред в статусе «open» (последний `needsAnswer`-ход агента
  без ответа человека) — переиспользуем существующую детекцию открытых тредов.

**`#btn-approve.onclick` — новый поток (гейт `/feature`):**
1. Если `hasFreeformAnswer || hasOpenThread` → показать быстрый инлайн-выбор:
   **«Применить и в бой»** vs **«Сначала на доработку»**.
   - «Применить и в бой» → `flushDraft()` (если черновик непуст) → `POST /signal approve-plan`.
   - «Сначала на доработку» → `flushDraft()` (обычная отправка), **без** approve.
2. Иначе (только чистые выборы) → `flushDraft()` (если непуст) → `approve-plan`. Один клик.

**Гейт `/improve` (выбор фич, `improveGate === featChoiceQuestions().length > 0`):**
сохраняем двухкликовый армированный конфирм (диспатч необратим).
- 1-й клик Approve → `flushDraft()` (авто-submit выборов) + перевод в `armed` с показом
  превью/счётчика `dispatchPreview`.
- 2-й клик → `approve-plan` (диспатч).
- Требование `submittedOnce` уходит (авто-выполняется через `flushDraft`).

**Чистка UI-степпера:** убрать нумерацию `①/②` (`#btn-submit::before`/`#btn-approve::before`),
нудж `toast.submitFirst` и ветку «unsent edits — Submit first» из `#btn-approve.onclick`.
Кнопка **«Отправить агенту на доработку»** остаётся как явный путь «хочу ревизию, не аппрувить».

### Orchestrator — `skills/feature/*`, `skills/improve/*`

На сигнале `approve-plan`: если в **том же** пробуждении `/wait` пришёл и новый сабмишн
(`submit.flag.latest > lastSubmission`) — сначала **применить** его ответы/правки к
`plan.md`/`questions.md`, затем **идти прямо в IMPLEMENT** (feature) / диспатч (improve),
**без** возврата в `awaiting-batch`. Формулировка «approve вбирает финальный сабмишн»:
- `skills/feature/feedback-loop.md` — раздел про обработку сабмишна + сигналов на одном wake.
- `skills/feature/phases.md` — секция плана-гейта (порядок: применить сабмишн → honor approve → IMPLEMENT).
- `skills/improve/dispatch-queue.md` — зеркально для диспатча.

### Контракт сервера

**Ноль правок `server.py`.** Эндпоинты `/submit` и `/signal` уже есть; меняется только
порядок их вызова на фронте. `/submit` и `/wait` уже будят агента; `approve-plan` приходит
на том же возврате `/wait` (оба счётчика — сабмишн и сигнал — бампаются). Оркестратор
обрабатывает сабмишн, затем honor `approve-plan`.

## Тесты

- Нет теста, пинящего текущее «submit-before-approve», — ломать нечего.
- **Добавить** тест-пин прозы нового контракта: в `feedback-loop.md`/`phases.md` присутствует
  формулировка «approve absorbs pending submission → advance directly» (по образцу
  `tests/test_review_gate_contract.py`, чистые file-reads).
- Сохранить существующие пины (`test_review_gate_contract`: `atomic`/`startedAt`/`re-run`;
  `test_shared_contract`: токен `approve-plan`) — при переформулировках не терять эти слова.
- Прогон: `python -m unittest discover -s tests -t .` — 347 зелёных как база.

## Доки / ADR

- Новый **ADR** (`docs/knowledge/decisions/`, англ.): гейт-политика — «Approve absorbs the
  pending draft (answers) and advances directly; ask-gate on free-form corrections / open
  threads; both gates; improve keeps its two-click irreversible-dispatch confirm». Обновить
  `INDEX.md`, `areas/dashboard-feedback-ui.md`, `areas/orchestrator-skills.md`, `task-log.md`.
  Связь: ADR-0008/0013 (агностичный бэкенд), ADR-0016 (ride-the-contract).

## Вне области (non-goals)

- Канал чата/якорных комментов не меняем — они и так шлются сразу.
- Никаких новых серверных эндпоинтов; ноль правок `server.py`.
- Автономный/eval-режим уже не паркуется на гейте — поведение не трогаем.
