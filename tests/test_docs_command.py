#!/usr/bin/env python3
"""Offline structural smoke tests for the `/docs` skill (stdlib unittest).

`/docs` ships authored markdown with a machine-checkable contract: the skill
`skills/docs/SKILL.md` (frontmatter `name: docs` + a non-empty `description`) and
its reference bundle (`phases.md`, `feedback-loop.md`, `dashboard-guide.md`,
`state-schema.md`, `knowledge-guide.md`, `parallel.md`), and a phases file that
names its stage machine. Frontmatter parsed hand-rolled (no PyYAML — stdlib CI).
No network, no disk writes.

Run with:
    python3 -m unittest tests.test_docs_command
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


class DocsSkillStructureTest(unittest.TestCase):
    def test_skill_frontmatter(self):
        skill = os.path.join(_REPO, "skills", "docs", "SKILL.md")
        self.assertTrue(os.path.isfile(skill), "skills/docs/SKILL.md must exist")
        with open(skill, "r", encoding="utf-8") as f:
            text = f.read()
        fm = "\n".join(_frontmatter(text))
        self.assertIn("name: docs", fm)
        self.assertTrue(any(ln.strip().startswith("description:")
                            for ln in _frontmatter(text)),
                        "frontmatter must have a description")

    def test_skill_reference_files(self):
        for ref in ("phases.md", "feedback-loop.md", "dashboard-guide.md",
                    "state-schema.md", "knowledge-guide.md", "parallel.md"):
            p = os.path.join(_REPO, "skills", "docs", ref)
            self.assertTrue(os.path.isfile(p), "skills/docs/%s must exist" % ref)

    def test_phases_cover_the_machine(self):
        with open(os.path.join(_REPO, "skills", "docs", "phases.md"),
                  "r", encoding="utf-8") as f:
            phases = f.read()
        for stage in ("INTAKE", "AUDIT", "COMPOSE", "PLAN GATE", "WRITE", "DONE"):
            self.assertIn(stage, phases, "phases.md must describe the %s stage" % stage)


if __name__ == "__main__":
    unittest.main()
