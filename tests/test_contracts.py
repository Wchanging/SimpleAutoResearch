from __future__ import annotations

import unittest

from simple_ar.core.contracts import CONTRACTS
from simple_ar.core.stages import Stage


class ContractTests(unittest.TestCase):
    def test_every_stage_has_a_contract(self) -> None:
        self.assertEqual(set(CONTRACTS), set(Stage))

    def test_contract_outputs_are_not_empty(self) -> None:
        for contract in CONTRACTS.values():
            self.assertTrue(contract.outputs, contract.stage.name)

    def test_run_contract_reports_real_run_artifacts(self) -> None:
        outputs = set(CONTRACTS[Stage.RUN].outputs)

        self.assertIn("results.json", outputs)
        self.assertIn("guard_report.json", outputs)
        self.assertIn("diagnosis.json", outputs)
        self.assertNotIn("contract.json", outputs)
        self.assertNotIn("report.md", outputs)


if __name__ == "__main__":
    unittest.main()
