from __future__ import annotations

import unittest
from pathlib import Path

from benchmark.arc_bench.batch_runner import _code_task_init_command


class ArcBenchBatchRunnerTests(unittest.TestCase):
    def test_init_command_overrides_config_output_root(self) -> None:
        repo_root = Path("repo")
        command = _code_task_init_command(
            repo_root=repo_root,
            config_path=repo_root / "prepared" / "ML01" / "code_task.toml",
            output_root=repo_root / "isolated-runs" / "ML01",
        )

        self.assertEqual(
            command,
            [
                "uv",
                "run",
                "simple-ar",
                "code-task",
                "init",
                "--config",
                "prepared/ML01/code_task.toml",
                "--output-root",
                "isolated-runs/ML01",
            ],
        )


if __name__ == "__main__":
    unittest.main()
