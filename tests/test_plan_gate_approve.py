#!/usr/bin/env python3
"""The plan-gate «Approve absorbs the pending answers» contract is pinned.

Answering questions / picking variants used to fill an unsent draft that **blocked**
«Утвердить план»; the only way to clear it was «Отправить агенту на доработку», forcing
a full revision round-trip before approval. Now Approve auto-submits the pending answers
and the orchestrator advances straight to IMPLEMENT/DISPATCH — no forced revision.

This has two surfaces with no other automated coverage:
  * the dashboard JS (`templates/dashboard.html`) — flush-on-approve + the ask-gate,
  * the orchestrator prose (`skills/**`) — apply-submission-then-advance, no re-park.

Both are pinned here so a reword/regression can't silently restore the old gate. Pure
file reads, no network/disk-writes.

Run with:
    python3 -m unittest tests.test_plan_gate_approve
    python3 -m unittest discover -s tests
"""

import os
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(_REPO, *parts), "r", encoding="utf-8") as f:
        return f.read()


class DashboardApproveGateTest(unittest.TestCase):
    def setUp(self):
        self.html = _read("templates", "dashboard.html")

    def test_approve_absorbs_draft_helpers_present(self):
        h = self.html
        # Approve flushes the draft itself, then signals — the two helpers that make it so.
        self.assertIn("function flushDraft", h)
        self.assertIn("async function doApprove", h)
        # The ask-gate (free-form correction / open thread) exists.
        self.assertIn("function needsApproveAsk", h)
        self.assertIn("approve-ask", h)          # the inline choice row
        self.assertIn("btn-apply-go", h)         # apply-and-implement
        self.assertIn("btn-revise-first", h)     # send-for-revision-first

    def test_old_submit_first_gate_is_gone(self):
        h = self.html
        # The removed Submit→Approve stepper: no session-latch, no "submit first" nudge.
        self.assertNotIn("submittedOnce", h)
        self.assertNotIn("toast.submitFirst", h)
        # Approve is no longer disabled by unsent edits.
        self.assertNotIn("const blocked = draftItems.length > 0", h)
        # (STR en/ru parity for the new actionbar.* keys is covered by test_settings.py's
        # dictionary-completeness invariant — not re-checked here.)


class OrchestratorProseTest(unittest.TestCase):
    def test_feature_gate_prose_says_approve_absorbs_and_no_repark(self):
        phases = _read("skills", "feature", "phases.md")
        feedback = _read("skills", "feature", "feedback-loop.md")
        self.assertIn("approve-plan", phases)
        self.assertIn("Approve absorbs", phases)
        self.assertIn("IMPLEMENT", phases)
        # feedback-loop must say: apply the submission, then advance without re-parking.
        self.assertIn("Approve absorbs", feedback)
        self.assertIn("re-park", feedback.lower())

    def test_improve_gate_prose_says_approve_absorbs_picks(self):
        guide = _read("skills", "improve", "dashboard-guide.md")
        feedback = _read("skills", "improve", "feedback-loop.md")
        self.assertIn("Approve absorbs", guide)      # no separate Submit needed
        self.assertIn("auto-submit", guide.lower())
        self.assertIn("Approve absorbs", feedback)


if __name__ == "__main__":
    unittest.main()
