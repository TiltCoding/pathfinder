#!/usr/bin/env python3
"""Offline structural smoke tests for the `/start` intent router (stdlib unittest).

`/start` is a single authored file (`skills/start/SKILL.md`) — a router, not a
machine, so it carries no reference bundle. The checkable contract: a valid
frontmatter (`name: start` + a non-empty `description`), and a routing body that
names every command it can route to, so the classifier stays in sync with the
installed orchestrators. No network, no disk writes.

Run with:
    python3 -m unittest tests.test_start_router
    python3 -m unittest discover -s tests
"""

import os
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _frontmatter(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    out = []
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        out.append(ln)
    return out


class StartRouterTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(_REPO, "skills", "start", "SKILL.md")
        self.assertTrue(os.path.isfile(self.path), "skills/start/SKILL.md must exist")
        with open(self.path, "r", encoding="utf-8") as f:
            self.text = f.read()

    def test_frontmatter(self):
        fm = "\n".join(_frontmatter(self.text))
        self.assertIn("name: start", fm)
        self.assertTrue(any(ln.strip().startswith("description:")
                            for ln in _frontmatter(self.text)),
                        "frontmatter must have a description")

    def test_routes_to_each_real_command(self):
        # the router must name every installed orchestrator it can hand off to, so
        # the classification table doesn't silently drift from what's registered
        for cmd in ("/ask", "/feature", "/new-product", "/improve", "/design", "/test"):
            self.assertIn(cmd, self.text, "router must mention %s" % cmd)

    def test_router_is_self_contained(self):
        # a router doesn't run the dashboard machine — it should NOT claim phases
        # it doesn't have; just sanity-check it describes routing, not a workflow
        self.assertIn("router", self.text.lower())


if __name__ == "__main__":
    unittest.main()
