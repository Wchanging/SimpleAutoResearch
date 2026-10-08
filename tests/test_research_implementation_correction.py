"""Research bridge to CodeTask's existing one-shot stale-anchor recovery."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from simple_ar.research.implementation import _implement_with_patch_correction


class ResearchImplementationCorrectionTests(unittest.TestCase):
    def test_retries_exactly_once_when_patch_validation_failed(self):
        for reason in ("patch_apply_failed", "edit_budget_rejected"):
            with self.subTest(reason=reason):
                first = SimpleNamespace(stop_reason=reason, steps=("failed",))
                second = SimpleNamespace(stop_reason=reason, steps=("still rejected",))
                messages = []
                with patch("simple_ar.research.implementation.implement_code_task", side_effect=[first, second]) as call:
                    outcome, steps = _implement_with_patch_correction(
                        Path("run"), approval_note="approved", message_callback=messages.append)
                self.assertIs(outcome, second)
                self.assertEqual(steps, ("failed", "still rejected"))
                self.assertEqual(call.call_count, 2)
                self.assertIn("retrying once", messages[0])
                self.assertIn("unchanged limits", messages[0])

    def test_does_not_retry_other_stop_reasons(self):
        first = SimpleNamespace(stop_reason="no_edits_proposed", steps=("blocked",))
        with patch("simple_ar.research.implementation.implement_code_task", return_value=first) as call:
            outcome, steps = _implement_with_patch_correction(Path("run"), approval_note="approved")
        self.assertIs(outcome, first)
        self.assertEqual(steps, ("blocked",))
        call.assert_called_once()
