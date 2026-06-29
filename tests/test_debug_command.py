#!/usr/bin/env python3
"""Offline structural smoke tests for the `/debug` skill (stdlib unittest).

`/debug` ships authored markdown with a machine-checkable contract: the skill
`skills/debug/SKILL.md` (frontmatter `name: debug` + a non-empty `description`)
and its reference bundle (`phases.md`, `feedback-loop.md`, `dashboard-guide.md`,
`state-schema.md`, `knowledge-guide.md`, `parallel.md`), plus `phases.md` must
name every stage of the reproduce->root-cause->fix->verify machine. Also pins
that `/start` routes a bug to `/debug` (the routing table was updated away from
the old `/feature` stop-gap). Frontmatter is parsed the same hand-rolled way as
tests/test_test_command.py (no PyYAML — CI is stdlib-only). No network, no disk
writes.

Run with:
    python3 -m unittest tests.test_debug_command
    python3 -m unittest discover -s tests
"""

import os
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _frontmatter_lines(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    out = []
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        out.append(ln)
    return out


class DebugSkillStructureTest(unittest.TestCase):
    def test_skill_frontmatter(self):
        skill = os.path.join(_REPO, "skills", "debug", "SKILL.md")
        self.assertTrue(os.path.isfile(skill), "skills/debug/SKILL.md must exist")
        with open(skill, "r", encoding="utf-8") as f:
            text = f.read()
        fm = _frontmatter_lines(text)
        self.assertTrue(fm, "SKILL.md must have a --- frontmatter block")
        joined = "\n".join(fm)
        self.assertIn("name: debug", joined)
        self.assertTrue(any(ln.strip().startswith("description:") for ln in fm),
                        "frontmatter must have a description")

    def test_skill_reference_files(self):
        for ref in ("phases.md", "feedback-loop.md", "dashboard-guide.md",
                    "state-schema.md", "knowledge-guide.md", "parallel.md"):
            p = os.path.join(_REPO, "skills", "debug", ref)
            self.assertTrue(os.path.isfile(p), "skills/debug/%s must exist" % ref)

    def test_phases_cover_the_machine(self):
        with open(os.path.join(_REPO, "skills", "debug", "phases.md"),
                  "r", encoding="utf-8") as f:
            phases = f.read()
        for stage in ("INTAKE", "REPRO", "DIAGNOSE", "ROOT-CAUSE GATE",
                      "FIX", "VERIFY", "DONE"):
            self.assertIn(stage, phases, "phases.md must describe the %s stage" % stage)

    def test_start_routes_bugs_to_debug(self):
        with open(os.path.join(_REPO, "skills", "start", "SKILL.md"),
                  "r", encoding="utf-8") as f:
            start = f.read()
        # the bug-routing row must now point at /debug (was a /feature stop-gap)
        self.assertIn("/debug", start, "/start must route bugs to /debug")


if __name__ == "__main__":
    unittest.main()
