import unittest

from simple_ar.code_task.review import _corroborated_review_findings
from simple_ar.code_task.reviewing import build_review_artifact
from simple_ar.reviewing.schema import ReviewFinding


def finding(cluster: str, summary: str, *, category: str = "logic") -> ReviewFinding:
    return ReviewFinding(
        severity="warning", category=category, summary=summary,
        evidence=["code_task/patch.diff"], source=f"code-task.llm-reviewer.{cluster}",
    )


class ReviewConsensusTests(unittest.TestCase):
    def test_phase_review_shares_one_interface_observation_with_all_clusters(self):
        import json
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        import simple_ar.code_task.review as review
        from simple_ar.code_task import initialize_code_task
        from simple_ar.core.artifacts import read_json
        for use_llm in (True, False):
            with self.subTest(use_llm=use_llm), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source = root / 'source'
                source.mkdir()
                (source / 'main.py').write_text('import helper\nhelper.missing()\n')
                (source / 'helper.py').write_text('def present():\n    return 42\n')
                task = root / 'task.md'
                task.write_text('Preserve the public interface\n')
                run = initialize_code_task(code_root=source, task_file=task, run_dir=root / 'run').run_dir
                prompts = []
                def capture(**kwargs):
                    context = kwargs['prompt'].split('Context JSON:\n', 1)[1].split('\n\nEvidence snippets:', 1)[0]
                    prompts.append(json.loads(context))
                    return []
                with patch.object(review, 'find_local_api_mismatches', wraps=review.find_local_api_mismatches) as scan, \
                     patch.object(review, 'project_api_contract', wraps=review.project_api_contract) as contract, \
                     patch.object(review, 'build_review_clusters', return_value=[{'cluster_id': 'first'}, {'cluster_id': 'second'}]), \
                     patch.object(review, 'snippets_for_cluster', return_value=['observed source']), \
                     patch.object(review, 'run_llm_review', side_effect=capture):
                    result = review.review_code_task_changes(run, use_llm=use_llm)
                self.assertEqual(scan.call_count, 1)
                self.assertEqual(contract.call_count, 1 if use_llm else 0)
                report = read_json(result.report_path)
                self.assertEqual(result.status, 'failed')
                self.assertTrue(any(row['category'] == 'interface_compatibility' for row in report['findings']))
                self.assertEqual(len(prompts), 2 if use_llm else 0)
                for context in prompts:
                    self.assertEqual(context['local_api_mismatches'][0]['missing_symbol'], 'missing')
                    self.assertIn('def present()', context['local_api_contract']['helper.py'])
                if prompts:
                    self.assertEqual(prompts[0]['local_api_mismatches'], prompts[1]['local_api_mismatches'])
                    self.assertEqual(prompts[0]['local_api_contract'], prompts[1]['local_api_contract'])

    def test_requests_for_downstream_validation_do_not_block_that_validation(self):
        rows = [finding(cluster, 'The applied fix has no recorded validation outcome', category='validation')
                for cluster in ('entrypoint', 'data_flow', 'core_logic', 'config_docs')]
        result = _corroborated_review_findings(rows)
        self.assertEqual(result, [])
        report = build_review_artifact(reviewer='code-task-reviewer', subject='post_apply', findings=rows)
        self.assertEqual(report['summary']['blocking_count'], 0)
        self.assertEqual(report['summary']['warning_count'], 1)

    def test_recorded_failed_validation_remains_a_deterministic_blocker(self):
        from pathlib import Path
        import tempfile
        from simple_ar.code_task import initialize_code_task
        from simple_ar.code_task.review import _deterministic_findings
        from simple_ar.core.artifacts import write_json
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'
            source.mkdir()
            (source / 'main.py').write_text('print("old")\n')
            task = root / 'task.md'
            task.write_text('Check behavior\n')
            initialized = initialize_code_task(code_root=source, task_file=task,
                                               run_dir=root / 'run')
            run = initialized.run_dir
            write_json(run / 'code_task/meta/validation_report.json', {'status': 'failed'})
            rows = _deterministic_findings(run, {}, [])
            self.assertTrue(any(row.key == 'validation:failed' and row.severity == 'blocking' for row in rows))

    def test_three_independent_matching_correctness_findings_block(self) -> None:
        rows = [
            finding("entrypoint", "New path is unreachable: no caller supplies the required argument."),
            finding("data_flow", "New path is unreachable: the active configuration omits the argument."),
            finding("metrics", "New path is unreachable: measured runs cannot enter the branch."),
        ]
        result = _corroborated_review_findings(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].severity, "blocking")
        self.assertEqual(result[0].source, "code-task.review-consensus")
        report = build_review_artifact(
            reviewer="code-task-reviewer", subject="post_apply", findings=[*rows, *result],
        )
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["summary"]["blocking_count"], 1)

    def test_single_source_unrelated_and_style_warnings_stay_advisory(self) -> None:
        self.assertEqual(_corroborated_review_findings([
            finding("entrypoint", "New path is unreachable: no caller."),
            finding("data_flow", "New path is unreachable: no caller."),
        ]), [])
        self.assertEqual(_corroborated_review_findings([
            finding("entrypoint", "New path is unreachable: no caller."),
            finding("entrypoint", "New path is unreachable: no caller."),
            finding("entrypoint", "New path is unreachable: no caller."),
        ]), [])
        self.assertEqual(_corroborated_review_findings([
            finding("entrypoint", "New path is unreachable: no caller."),
            finding("data_flow", "Argument order is wrong: values are swapped."),
            finding("metrics", "Validation command is absent: no run evidence."),
        ]), [])
        self.assertEqual(_corroborated_review_findings([
            finding(cluster, "Line wrapping is inconsistent: use the project style.", category="style")
            for cluster in ("entrypoint", "data_flow", "metrics")
        ]), [])


if __name__ == "__main__":
    unittest.main()
