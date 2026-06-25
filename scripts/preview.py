#!/usr/bin/env python3
"""Превью-харнесс дашборда: ставит фазовые фикстуры и открывает хаб.

Цель — в любой момент видеть **актуальный** вид дашборда, который генерят
агенты, во всех фазах, где он выглядит по-разному, без запуска настоящего
``/feature``.

Изоляция от рабочих дашбордов: превью НЕ трогает настоящий companion-сервер,
который обслуживает живые ``/feature``-``/ask`` прогоны. Оно поднимает **отдельный**
сервер с собственным корнем (``.preview/``) и на зарезервированном **редком порту**
(:9473, вне диапазона авто-скана 8473–8497), поэтому его reap (отбор по корню +
порту) физически не может убить главный сервер, а фикстуры не засоряют живой хаб.

Что делает (идемпотентно):

  1. копирует каждый ``templates/fixtures/<slug>/`` →
     ``.preview/.workflow/tasks/<slug>/`` (отдельный от живого корень);
  2. штампует **текущий** ``templates/dashboard.html`` как ``index.html`` каждой
     задачи — так превью всегда отражает живой шаблон;
  3. резолвит ``state.baseCommit`` вида ``AUTO:<rev>`` в реальный sha, чтобы
     вкладка «Изменения» показывала непустой diff (graceful: нет git → ключ
     убираем, сервер падает на ``HEAD``);
  4. (пере)поднимает изолированный companion-сервер на :9473 и открывает его
     ``/hub`` — оттуда кликом заходишь в любую фазовую фикстуру.

Только stdlib, кросс-платформенно (``sys.executable``).

Использование::

    python scripts/preview.py               # фикстуры + сервер + хаб
    python scripts/preview.py --no-browser   # не открывать браузер
    python scripts/preview.py --clean        # удалить _preview-* задачи и выйти
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES_DIR = os.path.join(ROOT, "templates", "fixtures")
TEMPLATE = os.path.join(ROOT, "templates", "dashboard.html")
SERVER = os.path.join(ROOT, "scripts", "server.py")

# Превью живёт в собственном корне на зарезервированном редком порту, чтобы не
# трогать настоящий сервер живых дашбордов (см. модульный docstring). Корень
# .preview/ — внутри репо (git -C находит работающее дерево для вкладки
# «Изменения»), но в .gitignore.
PREVIEW_ROOT = os.path.join(ROOT, ".preview")
PREVIEW_PORT = 9473
TASKS_DIR = os.path.join(PREVIEW_ROOT, ".workflow", "tasks")
SERVER_JSON = os.path.join(PREVIEW_ROOT, ".workflow", "server.json")


def fixtures():
    """Имена фикстур-каталогов под templates/fixtures/ (отсортированы)."""
    if not os.path.isdir(FIXTURES_DIR):
        return []
    return sorted(
        n for n in os.listdir(FIXTURES_DIR)
        if os.path.isdir(os.path.join(FIXTURES_DIR, n))
    )


def _git_rev(rev):
    """Резолв commit-ish в полный sha из корня репо; None если git/коммит нет."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", rev],
            cwd=ROOT, capture_output=True, text=True,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def _resolve_base_commit(state_path):
    """``state.baseCommit`` ``AUTO:<rev>`` → реальный sha (для непустого diff на
    вкладке «Изменения»). Если git недоступен — ключ убираем (graceful → HEAD).
    Правит только установленную копию в ``.workflow``, не фикстуру-источник."""
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return
    base = state.get("baseCommit")
    if not isinstance(base, str) or not base.startswith("AUTO:"):
        return
    sha = _git_rev(base[len("AUTO:"):].strip() or "HEAD")
    if sha:
        state["baseCommit"] = sha
    else:
        state.pop("baseCommit", None)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _sweep_legacy():
    """Снести фикстуры _preview-*, которые СТАРАЯ версия харнесса ставила прямо в
    живой ``ROOT/.workflow/tasks/`` и засоряла настоящий хаб. Идемпотентно: при
    апгрейде на изолированный корень это одноразово чистит живой хаб."""
    legacy = os.path.join(ROOT, ".workflow", "tasks")
    swept = 0
    for name in fixtures():
        d = os.path.join(legacy, name)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            swept += 1
    if swept:
        print(f"  * убрал из живого хаба старые превью-задачи: {swept}")


def install():
    """Поставить все фикстуры в изолированный .preview/.workflow/tasks/. Вернуть
    список slug'ов."""
    names = fixtures()
    if not names:
        print(f"нет фикстур в {FIXTURES_DIR}", file=sys.stderr)
        return []
    if not os.path.isfile(TEMPLATE):
        print(f"нет шаблона {TEMPLATE}", file=sys.stderr)
        return []
    _sweep_legacy()
    os.makedirs(TASKS_DIR, exist_ok=True)
    for name in names:
        src = os.path.join(FIXTURES_DIR, name)
        dst = os.path.join(TASKS_DIR, name)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        shutil.copyfile(TEMPLATE, os.path.join(dst, "index.html"))
        _resolve_base_commit(os.path.join(dst, "state.json"))
        print(f"  + {name}")
    return names


def clean():
    """Удалить установленные _preview-* задачи и весь изолированный корень
    .preview/ (включая его server.json). Прошлый превью-сервер на :9473 при
    следующем запуске реапнётся через --force; здесь его не трогаем."""
    removed = 0
    for name in fixtures():
        dst = os.path.join(TASKS_DIR, name)
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
            removed += 1
            print(f"  - {name}")
    _sweep_legacy()
    shutil.rmtree(PREVIEW_ROOT, ignore_errors=True)
    print(f"удалено превью-задач: {removed}")


def _spawn_server():
    """Запустить изолированный server.py отдельным фоновым процессом.

    Ключевая изоляция — ``--root PREVIEW_ROOT``: reap сервера отбирает жертв по
    корню, поэтому превью-сервер физически не может убить настоящий сервер живых
    дашбордов (у того корень ROOT). ``--port PREVIEW_PORT`` резервирует редкий
    порт вне диапазона авто-скана, так что чужие серверы тоже его не трогают.

    ``--force`` ОБЯЗАТЕЛЕН: без него идемпотентный singleton переиспользовал бы
    уже-живой превью-сервер, а он мог быть поднят из старой версии кода (из
    плагин-кэша) и рисовать устаревший хаб/логотип. Превью должно отражать
    ТЕКУЩИЙ код, поэтому реапаем прошлый превью-сервер (того же корня и порта) и
    биндим свежий. Не блокируемся на нём."""
    kwargs = {
        "cwd": ROOT,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        [sys.executable, SERVER, "--root", PREVIEW_ROOT,
         "--port", str(PREVIEW_PORT), "--no-browser", "--force"],
        **kwargs,
    )


def _alive(url):
    """True, если по url отвечает живой сервер (GET /health, короткий таймаут)."""
    try:
        with urllib.request.urlopen(url + "/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _server_url(timeout=15):
    """URL СВЕЖЕГО живого сервера. Ждём, пока server.json обновится после
    ``--force``-реапа И сервер реально отвечает (иначе вернули бы мёртвый url
    старого процесса в момент гонки реапа/бинда)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(SERVER_JSON, encoding="utf-8") as f:
                url = json.load(f).get("url")
            if url and _alive(url):
                return url
        except (OSError, ValueError):
            pass
        time.sleep(0.3)
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="preview.py",
        description="Превью дашборда: фазовые фикстуры + сервер + хаб.",
    )
    ap.add_argument("--no-browser", action="store_true",
                    help="не открывать браузер")
    ap.add_argument("--clean", action="store_true",
                    help="удалить _preview-* задачи и выйти")
    args = ap.parse_args(argv)

    if args.clean:
        clean()
        return 0

    print("ставлю фазовые фикстуры превью…")
    names = install()
    if not names:
        return 1

    _spawn_server()
    url = _server_url()
    if not url:
        print("сервер не поднялся вовремя — открой /hub вручную после старта",
              file=sys.stderr)
        return 1

    hub = url + "/hub"
    print(f"хаб:  {hub}")
    for n in names:
        print(f"      {url}/?slug={n}")
    if not args.no_browser:
        try:
            webbrowser.open(hub)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
