#!/usr/bin/env python3
"""The review-gate atomic-write + stale-running contract is documented (feat-16).

`reviews.json` is the only durable quality record of the VERIFY gates, written by
the orchestrator under an autonomous `/loop /feature` drain (a read-modify-write
into the shared store across sessions). Its contract — write atomically, carry a
`startedAt`, and treat a stale `running` as failed + re-run on resume — lives in
prose in the feature skill. This pins that prose so a reword can't silently drop
the invariant (the contract has no code surface to test otherwise). Pure file
reads, no network/disk-writes.

Run with:
    python3 -m unittest tests.test_review_gate_contract
    python3 -m unittest discover -s tests
"""

import os
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(_REPO, *parts), "r", encoding="utf-8") as f:
        return f.read()


class ReviewGateContractTest(unittest.TestCase):
    def setUp(self):
        self.phases = _read("skills", "feature", "phases.md")
        self.feedback = _read("skills", "feature", "feedback-loop.md")

    def test_phases_prescribes_atomic_and_stale_recovery(self):
        p = self.phases
        self.assertIn("reviews.json", p)
        # atomic write of the gate record
        self.assertIn("atomic", p.lower())
        self.assertTrue("_aipf" in p or "atomic_write" in p,
                        "phases.md must point at the atomic writer (_aipf/atomic_write)")
        # stale running -> failed + re-run gate on resume
        self.assertIn("startedAt", p)
        self.assertIn("re-run", p.lower())

    def test_feedback_loop_shape_carries_startedAt_and_atomic_note(self):
        f = self.feedback
        self.assertIn("reviews.json", f)
        self.assertIn("startedAt", f)       # the shape now includes it
        self.assertIn("atomic", f.lower())  # and the atomic-write note


if __name__ == "__main__":
    unittest.main()
