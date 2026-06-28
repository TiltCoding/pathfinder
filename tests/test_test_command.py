#!/usr/bin/env python3
"""Offline structural smoke tests for the `/test` skill (stdlib unittest).

`/test` ships authored markdown with a machine-checkable contract: the skill
`skills/test/SKILL.md` (frontmatter `name: test` + a non-empty `description`)
and its reference bundle (`phases.md`, `feedback-loop.md`, `dashboard-guide.md`,
`state-schema.md`, `knowledge-guide.md`, `parallel.md`). Frontmatter is parsed
the same hand-rolled way as tests/test_design.py / tests/test_ask.py (no PyYAML —
CI is stdlib-only). No network, no disk writes.

Run with:
    python3 -m unittest tests.test_test_command
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


class TestSkillStructureTest(unittest.TestCase):
    def test_skill_frontmatter(self):
        skill = os.path.join(_REPO, "skills", "test", "SKILL.md")
        self.assertTrue(os.path.isfile(skill), "skills/test/SKILL.md must exist")
        with open(skill, "r", encoding="utf-8") as f:
            text = f.read()
        fm = _frontmatter_lines(text)
        self.assertTrue(fm, "SKILL.md must have a --- frontmatter block")
        joined = "\n".join(fm)
        self.assertIn("name: test", joined)
        # a non-empty description key
        self.assertTrue(any(ln.strip().startswith("description:") for ln in fm),
                        "frontmatter must have a description")

    def test_skill_reference_files(self):
        for ref in ("phases.md", "feedback-loop.md", "dashboard-guide.md",
                    "state-schema.md", "knowledge-guide.md", "parallel.md"):
            p = os.path.join(_REPO, "skills", "test", ref)
            self.assertTrue(os.path.isfile(p), "skills/test/%s must exist" % ref)

    def test_phases_cover_the_machine(self):
        # the stage machine must name its phases so a resumed run knows them
        with open(os.path.join(_REPO, "skills", "test", "phases.md"),
                  "r", encoding="utf-8") as f:
            phases = f.read()
        for stage in ("INTAKE", "ANALYZE", "PLAN GATE", "IMPLEMENT", "VERIFY", "DONE"):
            self.assertIn(stage, phases, "phases.md must describe the %s stage" % stage)


if __name__ == "__main__":
    unittest.main()
