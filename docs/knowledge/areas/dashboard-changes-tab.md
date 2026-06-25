# Область: Вкладка «Изменения» дашборда

> Подсистема показа изменений рабочего дерева задачи: бэкенд-эндпоинты `/changes` (список) и
> `/changes?file=` (diff), фронтовое дерево файлов и встроенная подсветка синтаксиса в diff.

## Назначение

Показать во вкладке «Изменения» честный список файлов, реально изменённых задачей, как **сворачиваемое
дерево каталогов** (sidebar в стиле knowledge-tree), и открывать diff выбранного файла с **подсветкой
синтаксиса**. Историческая боль, которую чинила задача `changed-files-tree-view`: кириллические имена
ломались git C-quoting, untracked-каталог `docs/` сворачивался в одну строку `?? docs/`, в список лезли
пустые stray-файлы, diff был без подсветки.

## Ключевые файлы

### Бэкенд (`scripts/server.py`)

- `scripts/server.py:446` — `_git(*args, encoding="utf-8", errors="replace")`: общая обёртка git.
  Кодировка зашита в обёртку (см. подводные камни), а `-c core.quotePath=false` — **точечно** в командах,
  а не глобально (обёртка зовётся и для нейтральных `cat-file`/`rev-parse`).
- `scripts/server.py:466` — `_changes(slug)`: кеш 2 с + лок (не ломать), мягкая деградация (try/except).
- `scripts/server.py:482` — `_build_changes(slug)`: сборка плоского списка `files`. **Покрыт регресс-тестом
  `tests/test_changes.py`** (реальный git в tempdir, 14 кейсов: классификация/числа, `-uall`-развёртка,
  noise, выбор базы, worktree, graceful) — task-log `changes-endpoint-test` (2026-06-25). Источники —
  `git -c core.quotePath=false diff --numstat <base>` (`:489`, числа +/-) и
  `git -c core.quotePath=false status --porcelain --untracked-files=all` (`:511`, статусы, renames,
  развёрнутые untracked). **Переиспользуется** knowledge-графом (`_build_knowledge` зовёт его, чтобы
  пометить touched) — ключи `f.path` обязаны оставаться реальными относительными путями.
- `scripts/server.py:546` — `_is_noise(relpath, status)`: предикат «мусорный untracked» — 0-байтный
  новый файл. Tracked-изменения и непустые untracked не трогает.
- `scripts/server.py:565` — `_changes_file(slug, relpath)`: diff одного файла; untracked → diff против
  пустого блоба; traversal-guard на `relpath` (не ломать).

### Фронтенд (`templates/dashboard.html`)

- `:520` — `langFromPath(path)`: язык по расширению (`py/js/json/html/md/yaml/yml/css/sh/bash`),
  иначе пусто → без подсветки.
- `:540` — `highlightCode(line, lang)`: построчный токенайзер, работает **поверх `esc()`** (без CDN).
- `:912` — `renderChanges(ch, rv)`: фильтрация по тумблеру, сборка и рендер дерева, шапка с тумблером.
- `:961` — `buildFileTree(files)`: из плоского `files` строит узлы `{name,path,type,children,...}`
  группировкой по сегментам пути; каталоги выше файлов, имена алфавитно.
- `:991` — `renderChangeTree(node, depth)`: рекурсивный рендер узла дерева (классы `.tnode .dir/.file`).
- `:1008` — `toggleChangeDir(el)`: сворачивание каталога (toggle `.hidden` на `.tchildren`).
- `:1020` — `renderDiff(text, lang)`: классификация строк add/del/hunk/meta + подсветка тела строки.
- CSS токенов `:261` — `.tok-kw/.tok-str/.tok-com/.tok-num/.tok-fn/.tok-pn` (github-палитра, demo v2);
  segmented-тумблер «Только tracked / Все» в шапке.

## Публичный интерфейс

### `GET /changes?slug=<slug>` — список изменений

- Ответ: `{base, files:[{path, status, added, removed, untracked}], ...}`. `status` ∈
  `modified/added/deleted/renamed/…`; `added`/`removed` — числа строк (numstat). **`untracked: true`** —
  новый, никогда не стейдженный файл (нужно фронту для тумблера, чтобы не делать второй запрос).
- Кеш 2 с под локом. Мягкая деградация: при ошибке git — пустой/частичный список, не 500.

### `GET /changes?slug=<slug>&file=<relpath>` — diff одного файла

- Ответ: текст unified-diff. Untracked-файл диффится против пустого блоба. `relpath` проходит
  traversal-guard — выход за пределы рабочего дерева → `not found`.

## Инварианты

- **Кеш 2 с + лок** (`scripts/server.py:466`) и **traversal-guard** в `_changes_file` — не ломать.
- `_build_changes` остаётся read-only и отдаёт **реальные относительные пути** в `f.path` — контракт с
  `_build_knowledge` (пометка touched в knowledge-графе).
- `-c core.quotePath=false` ставится **точечно** на `diff --numstat` и `status`, не глобально в `_git`.
- Дерево строится **на фронте**; схема `/changes` плоская — backend почти нетронут (решение q4 в плане).
- Подсветка — **построчная** и всегда поверх `esc()`; токенайзер не вставляет неэкранированный текст.
- Состояние UI (`selectedFile`, `changesShowAll`) переживает polling-перерисовку (4 с) — не сбрасывать.

## Подводные камни

- **Кодировка — корень «сломанных имён».** `text=True` декодирует в локали процесса → на Windows cp1251
  портит кириллицу даже при `quotePath=false`. Поэтому `_git` форсирует `encoding="utf-8"` (как и везде
  в проекте — читать файлы строго UTF-8, не доверять консольному рендеру).
- **`core.quotePath=false` обязателен в numstat и status.** Иначе git C-кавычит не-ASCII пути
  (`\320\277…`). Флаг кроссплатформенный (проверено git 2.52 Windows).
- **`--untracked-files=all` (`-uall`) разворачивает каталоги.** Без него untracked-`docs/` выводится одной
  строкой `?? docs/` вместо реальных файлов.
- **Renames в numstat-ветке** имеют формат `old => new` в поле path — строка с ` => ` в numstat
  пропускается, числа/статус добирает status-ветка (иначе создаётся запись с битым путём).
- **0-байтные untracked — это реальные пустые файлы** (`-`, `Заполняется`, …), а не git-артефакт; их
  скрывает `_is_noise`. Скрытие управляется тумблером — по умолчанию «Только tracked».
- **Подсветка без внешней сети.** CDN/библиотеки запрещены → собственный лёгкий токенайзер (как `md()`).
  Покрывает реальные языки репозитория; неизвестные расширения остаются без подсветки.
- **Префикс diff вне токенайзера.** В `renderDiff` ведущий `+`/`-`/` ` снимается до `highlightCode` и
  возвращается после — иначе `-`/`+` путаются с операторами и ломают раскраску.

## Как расширять

- **Новый язык подсветки:** добавить расширение в `langFromPath` (`:520`) и его ключевые слова/правила
  комментариев в `highlightCode` (`:540`). Цвета — через токены `.tok-*` (`:261`).
- **Новое поле в списке файлов:** дополнить запись в `_build_changes` и потребление в `renderChanges`/
  `buildFileTree`; помнить про контракт `f.path` для knowledge-графа.
- **Другая политика untracked-мусора:** менять предикат `_is_noise` (`:546`), а не фильтровать на фронте;
  фронт лишь применяет тумблер по полю `untracked`.

_updated: 2026-06-25 (changes-endpoint-test: `_build_changes` покрыт `tests/test_changes.py`).
> ⚠ номера строк `scripts/server.py` в этом доке могли подрасти от дрейфа — `_build_changes` сейчас
> ближе к `:744`; ориентируйтесь по именам функций._
