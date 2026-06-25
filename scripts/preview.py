#!/usr/bin/env python3
"""Превью-харнесс дашборда: ставит фазовые фикстуры и открывает хаб.

Цель — в любой момент видеть **актуальный** вид дашборда, который генерят
агенты, во всех фазах, где он выглядит по-разному, без запуска настоящего
``/feature``.

Что делает (идемпотентно):

  1. копирует каждый ``templates/fixtures/<slug>/`` → ``.workflow/tasks/<slug>/``
     (источник истины коммитим, т.к. ``.workflow/`` в ``.gitignore``);
  2. штампует **текущий** ``templates/dashboard.html`` как ``index.html`` каждой
     задачи — так превью всегда отражает живой шаблон;
  3. резолвит ``state.baseCommit`` вида ``AUTO:<rev>`` в реальный sha, чтобы
     вкладка «Изменения» показывала непустой diff (graceful: нет git → ключ
     убираем, сервер падает на ``HEAD``);
  4. (пере)поднимает companion-сервер (идемпотентный singleton ``server.py``)
     и открывает ``/hub`` — оттуда кликом заходишь в любую фазовую фикстуру.

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
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES_DIR = os.path.join(ROOT, "templates", "fixtures")
TEMPLATE = os.path.join(ROOT, "templates", "dashboard.html")
TASKS_DIR = os.path.join(ROOT, ".workflow", "tasks")
SERVER = os.path.join(ROOT, "scripts", "server.py")
SERVER_JSON = os.path.join(ROOT, ".workflow", "server.json")


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


def install():
    """Поставить все фикстуры в .workflow/tasks/. Вернуть список slug'ов."""
    names = fixtures()
    if not names:
        print(f"нет фикстур в {FIXTURES_DIR}", file=sys.stderr)
        return []
    if not os.path.isfile(TEMPLATE):
        print(f"нет шаблона {TEMPLATE}", file=sys.stderr)
        return []
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
    """Удалить установленные _preview-* задачи из .workflow/tasks/."""
    removed = 0
    for name in fixtures():
        dst = os.path.join(TASKS_DIR, name)
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
            removed += 1
            print(f"  - {name}")
    print(f"удалено превью-задач: {removed}")


def _spawn_server():
    """Запустить server.py отдельным фоновым процессом (он идемпотентен: живой
    сервер переиспользуется, дубликат не плодится). Не блокируемся на нём."""
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
        [sys.executable, SERVER, "--root", ROOT, "--no-browser"], **kwargs
    )


def _server_url(timeout=12):
    """URL живого сервера из .workflow/server.json (ждём появления до timeout)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(SERVER_JSON, encoding="utf-8") as f:
                url = json.load(f).get("url")
            if url:
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
