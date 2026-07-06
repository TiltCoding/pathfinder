#!/usr/bin/env python3
"""The code-review wizard (REVIEW phase) is pinned across its three surfaces.

After a green VERIFY the orchestrator becomes the reviewer of its own diff: it
ranks the files/hunks it touched, publishes a `dashboard.json.review` object, and
parks on a dedicated «Ревью» tab where the human walks the change step by step,
comments on files/hunks via `rev:`-anchored threads, and the agent fixes + replies
on the same anchor until «Завершить ревью» (an `approve-plan` signal) ends it.

Three surfaces have no other automated coverage; a reword/regression would silently
break the wizard, so they are pinned here:

  1. Skill prose (`skills/feature/*.md`) — the REVIEW phase, the `review` field, the
     ranking heuristic, the anchored fix loop, and REVIEW's non-terminal/hub status.
     A guard also asserts the *older* VERIFY / plan-gate tokens still coexist, so the
     new phase augmented the prose rather than washing them out.
  2. Dashboard DOM/JS (`templates/dashboard.html`) — the tab, panel, controls,
     switchTab branch, static-string wiring, and the wizard render functions.
  3. STR en/ru — the review.* keys are present and paired in both languages.

Pure file reads, no network/disk-writes. This does NOT duplicate
`tests/test_review_gate_contract.py` (VERIFY atomic/stale-running record) or
`tests/test_plan_gate_approve.py` (the plan-gate absorb-answers contract).

Run with:
    python3 -m unittest tests.test_review_wizard
    python3 -m unittest discover -s tests -t .
"""

import os
import re
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DASHBOARD = os.path.join(_REPO, "templates", "dashboard.html")


def _read(*parts):
    with open(os.path.join(_REPO, *parts), "r", encoding="utf-8") as f:
        return f.read()


class ReviewProseTest(unittest.TestCase):
    """The REVIEW phase and its loop are described in the feature skill prose."""

    def setUp(self):
        self.phases = _read("skills", "feature", "phases.md")
        self.feedback = _read("skills", "feature", "feedback-loop.md")
        self.guide = _read("skills", "feature", "dashboard-guide.md")
        self.schema = _read("skills", "feature", "state-schema.md")

    def test_phases_has_review_between_verify_and_done(self):
        p = self.phases
        self.assertIn("REVIEW", p)
        # A dedicated REVIEW section sits between §6 VERIFY and §7 DONE.
        verify_at = p.index("VERIFY")
        review_at = p.index("REVIEW")
        done_at = p.rindex("DONE")
        self.assertLess(verify_at, review_at,
                        "REVIEW must come after VERIFY in phases.md")
        self.assertLess(review_at, done_at,
                        "REVIEW must come before DONE in phases.md")
        # It is the code-review wizard over the agent's own diff.
        self.assertIn("code-review wizard", p)

    def test_phases_review_describes_field_ranking_and_gate(self):
        p = self.phases
        # It publishes the `dashboard.json` `review` field.
        self.assertIn("dashboard.json.review", p)
        self.assertIn('"review"', p)
        # Files/blocks ranked by importance; array order is the ranking.
        self.assertIn("Rank", p)
        self.assertIn("blocks", p)
        self.assertIn("importance", p.lower())
        # Park on approve-plan (the «Завершить ревью» button).
        self.assertIn("approve-plan", p)
        self.assertIn("Завершить ревью", p)
        # Autonomous / eval publishes the structure but does NOT park.
        self.assertIn("Autonomous", p)
        self.assertRegex(
            p, r"do \*?\*?not\*?\*? park",
            "phases.md §REVIEW must state the autonomous/eval path does not park")

    def test_feedback_loop_describes_anchored_fix_reply_cycle(self):
        f = self.feedback
        # The review wizard cycle section exists.
        self.assertIn("Review wizard cycle", f)
        # Anchored threads keyed to rev: anchors.
        self.assertIn("rev:", f)
        # The agent replies on the SAME anchor.
        self.assertIn("same `anchor`", f)
        # A fix round bumps review.iteration.
        self.assertIn("review.iteration", f)
        # The comment -> fix -> reply loop (Edit / wf-coder + reply).
        self.assertIn("reply on the same", f)

    def test_dashboard_guide_describes_review_field_schema(self):
        g = self.guide
        self.assertIn('"review"', g)
        # Schema keys: steps, blocks, rank, kind, anchor.
        for key in ('"steps"', '"blocks"', '"rank"', '"kind"', '"anchor"'):
            self.assertIn(key, g, f"dashboard-guide.md must document {key}")

    def test_state_schema_says_review_nonterminal_and_active_in_hub(self):
        s = self.schema
        self.assertIn("REVIEW", s)
        # Non-terminal, stays active in the hub while the wizard runs.
        self.assertIn("non-terminal", s.lower())
        self.assertIn("hub", s.lower())

    def test_older_verify_and_plan_gate_tokens_still_coexist(self):
        # Guard: the new REVIEW prose must AUGMENT, not replace, the VERIFY and
        # plan-gate contracts (pinned separately by test_review_gate_contract.py /
        # test_plan_gate_approve.py). If a reword washed these out, those tests would
        # also fail — this asserts the tokens still live alongside REVIEW here.
        for tok in ("reviews.json", "startedAt", "approve-plan"):
            self.assertIn(tok, self.phases,
                          f"phases.md lost VERIFY/plan-gate token {tok!r}")
        for tok in ("reviews.json", "startedAt", "approve-plan"):
            self.assertIn(tok, self.feedback,
                          f"feedback-loop.md lost VERIFY/plan-gate token {tok!r}")


class ReviewDashboardDomTest(unittest.TestCase):
    """The «Ревью» tab, its controls, and its render functions live in the template."""

    def setUp(self):
        self.html = _read("templates", "dashboard.html")

    def test_tab_panel_and_controls_present(self):
        h = self.html
        for ident in ('id="tab-review"', 'id="review"', 'id="btn-review-done"',
                      'id="rv-prev"', 'id="rv-next"'):
            self.assertIn(ident, h, f"missing DOM id/attr: {ident}")

    def test_switchtab_and_static_strings_wire_review(self):
        h = self.html
        # switchTab toggles the #review panel on the "review" tab.
        self.assertRegex(
            h, r'#review"?\)?\.hidden\s*=\s*name\s*!==\s*"review"',
            "switchTab must gate #review on the review tab")
        # applyStaticStrings sets the tab label from t("tab.review").
        self.assertIn('set("#tab-review"', h)

    def test_wizard_render_functions_present(self):
        h = self.html
        for fn in ("renderReview", "reviewTick", "gotoStep", "hunkSlice",
                   "kindChip", "renderReviewRail", "renderReviewStep",
                   "renderReviewStepper"):
            self.assertIn("function " + fn, h,
                          f"missing wizard function: {fn}")

    def test_screen_reader_announce_used_in_review_code(self):
        # Soft check: the review step code announces the current step to a
        # screen reader via #phase-announce. We assert the live-region id exists
        # and is referenced near a review function (gotoStep writes to it).
        h = self.html
        self.assertIn('id="phase-announce"', h)
        # #phase-announce is referenced inside the review/step code block.
        goto_at = h.index("function gotoStep")
        # the next few hundred chars of gotoStep write the step into the region
        window = h[goto_at:goto_at + 800]
        self.assertIn("phase-announce", window,
                      "gotoStep should announce the step via #phase-announce")


# The STR extractor mirrors tests/test_settings.py: a pragmatic, line-based scan
# of the inline `const STR = { en:{…}, ru:{…} };` block. Kept local so this file
# stays self-contained (no import from another test module).
def _extract_str_keys(text):
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if re.match(r"\s*const STR\s*=\s*\{", ln)), None)
    assert start is not None, "`const STR = {` block not found"
    en_at = ru_at = end_at = None
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if en_at is None and re.match(r"\s*en:\s*\{", ln):
            en_at = i
        elif ru_at is None and re.match(r"\s*ru:\s*\{", ln):
            ru_at = i
        elif ru_at is not None and re.match(r"\s*\};", ln):
            end_at = i
            break
    assert en_at is not None, "STR.en section not found"
    assert ru_at is not None, "STR.ru section not found"
    assert end_at is not None, "STR closing `};` not found"
    key_re = re.compile(r'^\s*"([\w.]+)"\s*:')

    def keys(lo, hi):
        out = set()
        for ln in lines[lo + 1:hi]:
            m = key_re.match(ln)
            if m:
                out.add(m.group(1))
        return out

    return keys(en_at, ru_at), keys(ru_at, end_at)


class ReviewStrKeysTest(unittest.TestCase):
    """The review.* / tab.review keys exist and are paired in both languages.

    test_settings.py already asserts FULL en/ru key-set parity for the whole STR
    dictionary, so we don't re-check global completeness here. We assert the
    review family specifically: the wizard's keys are present in both languages
    and the `review.*` key sets match between en and ru (no key in only one)."""

    def setUp(self):
        self.en, self.ru = _extract_str_keys(_read("templates", "dashboard.html"))

    def test_core_review_keys_present_in_both(self):
        for key in ("tab.review", "review.done", "review.step"):
            self.assertIn(key, self.en, f"{key} missing from STR.en")
            self.assertIn(key, self.ru, f"{key} missing from STR.ru")

    def test_review_key_sets_match_between_en_and_ru(self):
        en_rev = {k for k in self.en if k == "tab.review" or k.startswith("review.")}
        ru_rev = {k for k in self.ru if k == "tab.review" or k.startswith("review.")}
        self.assertTrue(en_rev, "no review.* keys extracted from STR.en — extractor stale?")
        only_en = sorted(en_rev - ru_rev)
        only_ru = sorted(ru_rev - en_rev)
        self.assertEqual(
            (only_en, only_ru), ([], []),
            f"review.* STR keys diverge — en-only={only_en} ru-only={only_ru}")


if __name__ == "__main__":
    unittest.main()
