#!/usr/bin/env python3
"""Offline parity/completeness checks for the theme + status styling that
HUB_PAGE (in server.py) hand-mirrors from templates/dashboard.html
(stdlib unittest, no network).

The STR i18n dictionary already has a completeness test (test_settings.py); this
guards the *other* hand-maintained surface the knowledge base flags as a silent
drift risk: the two `:root[data-theme=...]` theme palettes and the header
status-badge classes, duplicated between dashboard.html and HUB_PAGE.

Checks (pragmatic regex extractors, deliberately not a CSS parser):

  * Per-file palette completeness (the ADR-0015 invariant, for CSS tokens): in
    EACH file the set of `--token` names defined under `:root[data-theme="light"]`
    must equal the set under `:root[data-theme="dark"]` — a token added to one
    theme but not the other is a broken color in that theme.
  * The shared header status-badge states `working` / `awaiting` (the ones
    HUB_PAGE mirrors from dashboard.html) are styled in BOTH files.

Run-card / queue statuses (done, failed, pending, skipped, inProgress) and the
hub-only `--awaiting-soft` token are intentionally NOT required to match: they
are context-specific, not a mirror.

Run:
    python -m unittest tests.test_hub_dashboard_parity -v
    python -m unittest discover -s tests
"""
import os
import re
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DASHBOARD = os.path.join(_REPO, "templates", "dashboard.html")
_SERVER = os.path.join(_REPO, "scripts", "server.py")

# core header status-badge states both files render (dashboard's #status badge;
# HUB_PAGE mirrors them — see the "mirrors dashboard.html .status.working/.awaiting"
# comment in server.py). Run/queue statuses are excluded on purpose.
_CORE_STATUS = {"working", "awaiting"}


def _theme_tokens(text, theme, *, source):
    """Set of `--token` names defined under `:root[data-theme="<theme>"]`.

    Assumes a flat token block (true of both files): `:root[data-theme="x"] {
    --a: v; --b: v; }`, closed by the first `}` (custom-property values carry no
    braces). Raises AssertionError naming `source` if the block isn't found."""
    m = re.search(r':root\[data-theme="%s"\]\s*\{(.*?)\}' % re.escape(theme),
                  text, re.S)
    assert m is not None, f"{source}: :root[data-theme=\"{theme}\"] block not found"
    return set(re.findall(r'(--[\w-]+)\s*:', m.group(1)))


def _status_states(text):
    """Set of `.status.<state>` class names styled in the file."""
    return set(re.findall(r'\.status\.([A-Za-z][\w-]*)', text))


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class ThemePaletteCompletenessTest(unittest.TestCase):
    def _assert_palette_complete(self, text, label):
        light = _theme_tokens(text, "light", source=label)
        dark = _theme_tokens(text, "dark", source=label)
        self.assertTrue(light, f"{label}: extracted no light theme tokens — extractor stale?")
        self.assertTrue(dark, f"{label}: extracted no dark theme tokens — extractor stale?")
        only_light = sorted(light - dark)
        only_dark = sorted(dark - light)
        self.assertEqual(
            (only_light, only_dark), ([], []),
            f"{label}: light/dark theme palettes diverge — "
            f"light-only={only_light} dark-only={only_dark}")

    def test_dashboard_palette_complete(self):
        self._assert_palette_complete(_read(_DASHBOARD), "dashboard.html")

    def test_hub_palette_complete(self):
        self._assert_palette_complete(_read(_SERVER), "server.py HUB_PAGE")


class StatusBadgeParityTest(unittest.TestCase):
    def test_core_status_states_in_both_files(self):
        dash = _status_states(_read(_DASHBOARD))
        hub = _status_states(_read(_SERVER))
        self.assertTrue(dash and hub, "extractor stale — no status states found")
        missing_dash = sorted(_CORE_STATUS - dash)
        missing_hub = sorted(_CORE_STATUS - hub)
        self.assertEqual(
            (missing_dash, missing_hub), ([], []),
            f"core status badge states drifted — missing in dashboard.html="
            f"{missing_dash}, missing in HUB_PAGE={missing_hub}")


if __name__ == "__main__":
    unittest.main()
