#!/usr/bin/env python3
"""Offline structural smoke tests for the `/design` skill + agents (stdlib unittest).

`/design` ships three authored artifacts that have machine-checkable contracts:

  * the skill `skills/design/SKILL.md` (frontmatter `name: design` + a non-empty
    `description`) and its reference bundle (`phases.md`, `feedback-loop.md`,
    `dashboard-guide.md`, `state-schema.md`);
  * the read-only stage-1 auditor `agents/ds-auditor.md` — `name: ds-auditor`,
    `tools:` grants Read/Grep/Glob but NOT Write/Edit, and carries no `model:`
    key (model is global per subagent_type — ADR-0006);
  * the stage-2 builder `agents/ds-coder.md` — `name: ds-coder`, `tools:` grants
    Write and Edit, and likewise no `model:` key.

Frontmatter is parsed the same hand-rolled way as `tests/test_ask.py` (line/string
checks) — CI is stdlib-only, no PyYAML. No network and no disk writes.

Run with:
    python3 -m unittest tests.test_design
    python3 -m unittest discover -s tests   # full suite
"""

import os
import unittest

# Resolve the repo root from this file, exactly like tests/test_ask.py.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _frontmatter_lines(text):
    """The lines of the leading `---`...`---` YAML frontmatter block.

    Hand-rolled (no PyYAML) to match this project's stdlib-only test tooling.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    out = []
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        out.append(ln)
    return out


def _field(lines, key):
    """The raw value of a single `key: value` frontmatter line, or None."""
    prefix = key + ":"
    for ln in lines:
        if ln.strip().startswith(prefix):
            return ln.strip()[len(prefix):].strip()
    return None


class SkillSmokeTest(unittest.TestCase):
    """Structural smoke checks for the /design skill + agents."""

    def test_skill_frontmatter(self):
        skill = os.path.join(_REPO, "skills", "design", "SKILL.md")
        self.assertTrue(os.path.isfile(skill),
                        "skills/design/SKILL.md must exist")
        with open(skill, "r", encoding="utf-8") as f:
            text = f.read()
        fm = _frontmatter_lines(text)
        self.assertIn("name: design", text)
        # description may be a `>-` folded block: the line after `description:`
        # carries the first body line. Either way, something non-empty follows.
        idx = next((i for i, ln in enumerate(fm)
                    if ln.strip().startswith("description:")), -1)
        self.assertGreaterEqual(idx, 0, "frontmatter must have a description")
        desc = _field(fm, "description") or ""
        if desc in ("", ">-", ">", "|", "|-"):
            # folded/literal block — assert the continuation line is non-empty
            self.assertTrue(any(ln.strip() for ln in fm[idx + 1:]),
                            "description block must be non-empty")
        else:
            self.assertTrue(desc.strip(), "description must be non-empty")

    def test_skill_reference_files(self):
        skill_dir = os.path.join(_REPO, "skills", "design")
        for ref in ("phases.md", "feedback-loop.md",
                    "dashboard-guide.md", "state-schema.md"):
            path = os.path.join(skill_dir, ref)
            self.assertTrue(os.path.isfile(path),
                            "skills/design/%s must exist" % ref)

    def test_ds_auditor_is_read_only(self):
        agent = os.path.join(_REPO, "agents", "ds-auditor.md")
        self.assertTrue(os.path.isfile(agent),
                        "agents/ds-auditor.md must exist")
        with open(agent, "r", encoding="utf-8") as f:
            text = f.read()
        fm = _frontmatter_lines(text)
        self.assertEqual(_field(fm, "name"), "ds-auditor")
        tools_line = _field(fm, "tools") or ""
        for tool in ("Read", "Grep", "Glob"):
            self.assertIn(tool, tools_line,
                          "ds-auditor tools must include %s" % tool)
        # Read-only: the tools grant must not include write access.
        self.assertNotIn("Write", tools_line)
        self.assertNotIn("Edit", tools_line)
        # Model is global per subagent_type (ADR-0006) — no `model:` override.
        self.assertIsNone(_field(fm, "model"),
                          "ds-auditor must not pin a model")

    def test_ds_coder_can_write(self):
        agent = os.path.join(_REPO, "agents", "ds-coder.md")
        self.assertTrue(os.path.isfile(agent),
                        "agents/ds-coder.md must exist")
        with open(agent, "r", encoding="utf-8") as f:
            text = f.read()
        fm = _frontmatter_lines(text)
        self.assertEqual(_field(fm, "name"), "ds-coder")
        tools_line = _field(fm, "tools") or ""
        self.assertIn("Write", tools_line)
        self.assertIn("Edit", tools_line)
        # Model is global per subagent_type (ADR-0006) — no `model:` override.
        self.assertIsNone(_field(fm, "model"),
                          "ds-coder must not pin a model")


if __name__ == "__main__":
    unittest.main()
