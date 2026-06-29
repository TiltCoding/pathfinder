#!/usr/bin/env python3
"""Кросс-платформенный dev-раннер ai-pathfinder (только stdlib).

Единый путь для тестов и сервера без зависимости от ``make`` и имени ``python3``
(использует ``sys.executable``, поэтому одинаково работает на Windows и *nix).

Использование::

    python dev.py test [цели unittest...]
    python dev.py serve [--port N] [--open SLUG] [--no-browser] [--no-forward]
    python dev.py preview [--no-browser] [--clean]
    python dev.py lint
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def _run(cmd):
    """Запустить подпроцесс из корня проекта, вернуть его код возврата."""
    return subprocess.call(cmd, cwd=ROOT)


def cmd_test(args):
    base = [sys.executable, "-m", "unittest"]
    if args.targets:
        return _run(base + args.targets)
    # -t . (top-level dir = repo root) so `tests` is imported as a package and
    # tests/__init__.py runs first, putting scripts/ on sys.path for every module.
    return _run(base + ["discover", "-s", "tests", "-t", "."])


def cmd_serve(args):
    cmd = [sys.executable, os.path.join("scripts", "server.py"), "--root", ROOT]
    if args.port:
        cmd += ["--port", str(args.port)]
    if args.open:
        cmd += ["--open", args.open]
    if args.no_browser:
        cmd.append("--no-browser")
    if args.no_forward:
        cmd.append("--no-forward")
    return _run(cmd)


def cmd_preview(args):
    cmd = [sys.executable, os.path.join("scripts", "preview.py")]
    if args.no_browser:
        cmd.append("--no-browser")
    if args.clean:
        cmd.append("--clean")
    return _run(cmd)


def cmd_queue(args):
    """Pass through to the dispatch-queue CLI (scripts/queue.py).

    Terminal visibility into the /improve drain queue without a browser:
    `python dev.py queue status` (default), or any queue.py subcommand."""
    rest = args.rest or ["status"]
    return _run([sys.executable, os.path.join("scripts", "queue.py")] + rest)


def cmd_lint(args):
    checker = os.path.join(ROOT, "scripts", "check_stdlib.py")
    if os.path.exists(checker):
        return _run([sys.executable, checker])
    print(
        "линт-гейт (scripts/check_stdlib.py) ещё не добавлен — "
        "появится в фиче stdlib-invariant-lint-gate (feat-8)."
    )
    return 0


def cmd_check(args):
    """Run both CI gates locally: the test suite, then the stdlib lint. Returns
    non-zero on the first failure — a one-command pre-push mirror of CI's two
    jobs, so a red lint isn't discovered only after pushing green tests."""
    rc = cmd_test(argparse.Namespace(targets=[]))
    if rc != 0:
        return rc
    return cmd_lint(argparse.Namespace())


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="dev.py",
        description="Кросс-платформенный раннер ai-pathfinder (stdlib, sys.executable).",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p_test = sub.add_parser("test", help="прогнать тесты (python -m unittest)")
    p_test.add_argument(
        "targets",
        nargs="*",
        help="конкретные цели unittest (по умолчанию: discover -s tests)",
    )
    p_test.set_defaults(func=cmd_test)

    p_serve = sub.add_parser("serve", help="поднять companion-сервер")
    p_serve.add_argument("--port", type=int, default=0, help="желаемый порт (0 = авто)")
    p_serve.add_argument("--open", default="", help="slug задачи, открыть в браузере")
    p_serve.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    p_serve.add_argument("--no-forward", action="store_true", help="не форвардить в Langfuse")
    p_serve.set_defaults(func=cmd_serve)

    p_preview = sub.add_parser(
        "preview",
        help="поставить фазовые фикстуры дашборда и открыть хаб",
    )
    p_preview.add_argument("--no-browser", action="store_true",
                           help="не открывать браузер")
    p_preview.add_argument("--clean", action="store_true",
                           help="удалить _preview-* задачи и выйти")
    p_preview.set_defaults(func=cmd_preview)

    p_queue = sub.add_parser("queue",
                             help="статус/драйв очереди дренажа /improve (scripts/queue.py)")
    p_queue.add_argument("rest", nargs=argparse.REMAINDER,
                         help="подкоманда queue.py (по умолчанию: status)")
    p_queue.set_defaults(func=cmd_queue)

    p_lint = sub.add_parser("lint", help="линт-гейт stdlib-инвариантов (стаб до feat-8)")
    p_lint.set_defaults(func=cmd_lint)

    p_check = sub.add_parser("check", help="оба CI-гейта локально: тесты + линт")
    p_check.set_defaults(func=cmd_check)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
