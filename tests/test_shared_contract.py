#!/usr/bin/env python3
"""Offline structural test for the shared dashboard/feedback contract.

The invariant contract every workflow shares (companion server, dashboard.json
schema + endpoints, the Submit->Approve gate, shared state.json fields) now lives
in one canonical `skills/_shared/dashboard-contract.md`. These pin that the file
exists and is substantive, and that every workflow SKILL.md points at it (so the
per-skill `feedback-loop.md`/`dashboard-guide.md`/`state-schema.md` defer to one
source for the core). `/start` is a router with no reference bundle and is
exempt. stdlib unittest, no network/disk writes.

Run with:
    python3 -m unittest tests.test_shared_contract
    python3 -m unittest discover -s tests
"""

import glob
import os
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SHARED = os.path.join(_REPO, "skills", "_shared", "dashboard-contract.md")
# workflows that carry the dashboard/feedback contract (router /start is exempt)
_WORKFLOWS = ("ask", "debug", "design", "docs", "feature", "improve",
              "new-product", "test")


class SharedContractTest(unittest.TestCase):
    def test_canonical_file_exists_and_is_substantive(self):
        self.assertTrue(os.path.isfile(_SHARED),
                        "skills/_shared/dashboard-contract.md must exist")
        with open(_SHARED, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertGreater(len(text), 1500, "shared contract looks too thin")
        # it must actually describe the core surfaces it claims to canonicalize
        for token in ("dashboard.json", "/wait", "approve-plan", "state.json", "ETag"):
            self.assertIn(token, text, "shared contract must cover %r" % token)

    def test_every_workflow_skill_references_it(self):
        for wf in _WORKFLOWS:
            skill = os.path.join(_REPO, "skills", wf, "SKILL.md")
            self.assertTrue(os.path.isfile(skill), "%s SKILL.md must exist" % wf)
            with open(skill, "r", encoding="utf-8") as f:
                text = f.read()
            self.assertIn("_shared/dashboard-contract.md", text,
                          "%s/SKILL.md must reference the shared contract" % wf)

    def test_router_start_is_not_forced_to_reference(self):
        # /start has no reference bundle; it should be left alone (no false pointer)
        skill = os.path.join(_REPO, "skills", "start", "SKILL.md")
        if os.path.isfile(skill):
            with open(skill, "r", encoding="utf-8") as f:
                self.assertNotIn("_shared/dashboard-contract.md", f.read())

    def test_no_workflow_skill_lost_its_local_bundle(self):
        # the dedup is additive — per-skill files still present (no info loss)
        for wf in _WORKFLOWS:
            for ref in ("feedback-loop.md", "dashboard-guide.md", "state-schema.md"):
                p = os.path.join(_REPO, "skills", wf, ref)
                self.assertTrue(os.path.isfile(p),
                                "%s/%s must still exist" % (wf, ref))


if __name__ == "__main__":
    unittest.main()
