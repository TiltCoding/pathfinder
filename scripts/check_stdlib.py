#!/usr/bin/env python3
"""Линт-гейт инвариантов ai-pathfinder (только stdlib).

Проверяет два несущих инварианта проекта (`docs/knowledge/conventions.md`):
  1. `scripts/*.py` импортируют только stdlib (+ локальные модули проекта, напр. `_aipf`).
  2. `templates/*.html` не тянут внешний контент (CDN): нет `src=`/`href=` на
     `http(s)://` или protocol-relative `//`, и нет `@import url(http…)`.

Выход: 0 — чисто; 1 — есть нарушения (печатаются как `path:line  сообщение`).
Запуск: `python scripts/check_stdlib.py` (подхватывается `dev.py lint` и CI).
"""
import ast
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # репо-корень (scripts/ → ..)
SCRIPTS = os.path.join(ROOT, "scripts")
TEMPLATES = os.path.join(ROOT, "templates")


def _local_module_names():
    """Имена локальных модулей в scripts/ (стемы *.py) — их импорт легитимен."""
    return {
        os.path.splitext(os.path.basename(p))[0]
        for p in glob.glob(os.path.join(SCRIPTS, "*.py"))
    }


def check_stdlib_imports():
    """Нарушения: импорт не-stdlib и не-локального модуля в scripts/*.py."""
    allowed = set(sys.stdlib_module_names) | _local_module_names()
    violations = []
    for path in sorted(glob.glob(os.path.join(SCRIPTS, "*.py"))):
        rel = os.path.relpath(path, ROOT)
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in allowed:
                        violations.append((rel, node.lineno, "non-stdlib import: " + alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # относительный импорт — локальный, пропуск
                    continue
                if node.module:
                    top = node.module.split(".")[0]
                    if top not in allowed:
                        violations.append((rel, node.lineno, "non-stdlib import: from " + node.module))
    return violations


_CDN_PATTERNS = (
    re.compile(r"""(?:src|href)\s*=\s*['"](?:https?:)?//""", re.I),
    re.compile(r"""@import\s+(?:url\()?\s*['"]?https?:""", re.I),
)


def check_no_cdn():
    """Нарушения: внешние src/href или @import http в templates/*.html."""
    violations = []
    for path in sorted(glob.glob(os.path.join(TEMPLATES, "*.html"))):
        rel = os.path.relpath(path, ROOT)
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if any(p.search(line) for p in _CDN_PATTERNS):
                    violations.append((rel, i, "external/CDN reference: " + line.strip()[:80]))
    return violations


def main():
    violations = check_stdlib_imports() + check_no_cdn()
    if violations:
        print("FAIL: нарушены stdlib/no-CDN инварианты:")
        for rel, lineno, msg in violations:
            print("  {}:{}  {}".format(rel, lineno, msg))
        return 1
    print("OK: scripts/ — только stdlib; templates/ — без CDN.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
