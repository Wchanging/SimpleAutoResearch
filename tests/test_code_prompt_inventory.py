import copy
import json
import unittest

from simple_ar.code_task.editing.scope import prompt_file_inventory, is_edit_allowed_path
from simple_ar.code_task.editing.planning import _plan_user_prompt
from simple_ar.code_task.editing.work_plan import _work_plan_user_prompt
from simple_ar.code_task.editing.patching import _edit_user_prompt
from simple_ar.code_task.editing.budget import edit_budget_for_profile


class PromptInventoryTests(unittest.TestCase):
    def index(self):
        return {'project': {'name': 'arbitrary-project'}, 'files': [
            {'path': 'src/feature.py', 'kind': 'python', 'role_tags': ['source'],
             'summary': 'functions: solve', 'python': {'functions': [{'name': 'unseen_signature'}]}},
            {'path': 'src/test_protected.py', 'kind': 'python', 'summary': 'selected-test-evidence'},
            {'path': 'other/foreign.py', 'kind': 'python', 'summary': 'outside-edit-scope'},
            *[{'path': f'tests/fixtures/check_{i}.sql', 'kind': 'text',
               'summary': 'irrelevant-fixture-summary-' + 'x' * 150} for i in range(1500)],
        ]}

    def test_complete_discovery_and_existing_roles_without_unbounded_signatures(self):
        index = self.index()
        before = copy.deepcopy(index)
        inventory = prompt_file_inventory(index, allowed_patterns=('src/**',),
            protected_patterns=('tests/**', '**/test_*.py'), selected_paths=['src/test_protected.py'])
        self.assertEqual(index, before)
        self.assertEqual(inventory['editable_files'], ['src/feature.py'])
        self.assertEqual(set(inventory['editable_files'] + inventory['read_only_files']),
                         {row['path'] for row in index['files']})
        self.assertEqual({row['path'] for row in inventory['file_details']},
                         {'src/feature.py', 'src/test_protected.py'})
        for row in index['files']:
            self.assertEqual(row['path'] in inventory['editable_files'], is_edit_allowed_path(
                row['path'], allowed_patterns=('src/**',), protected_patterns=('tests/**', '**/test_*.py')))
        text = json.dumps(inventory)
        self.assertNotIn('unseen_signature', text)
        self.assertNotIn('irrelevant-fixture-summary', text)
        self.assertIn('selected-test-evidence', text)
        self.assertLess(len(text), len(json.dumps(index)) / 3)

    def test_all_three_prompts_share_inventory_and_preserve_exact_source_evidence(self):
        index = self.index()
        snippets = [{'path': 'src/feature.py', 'text': 'def solve(value):\n    return value + 1\n',
                     'access_role': 'editable', 'truncated': False}]
        reference = {'path': 'src/test_protected.py', 'text': 'def test_evidence():\n    assert True\n',
                     'access_role': 'reference', 'truncated': False}
        common = dict(task_text='Fix a reported problem', index=index,
            allowed_patterns=('src/**',), protected_patterns=('tests/**', '**/test_*.py'), memory_context='')
        prompts = [
            _plan_user_prompt(**common, snippets=[*snippets, reference], benchmark_command='validate', run_context={}),
            _work_plan_user_prompt(**common, snippets=[*snippets, reference], benchmark_command='validate', run_context={}),
            _edit_user_prompt(**common, snippets=snippets, reference_snippets=[reference], patch_plan='Accepted plan',
                read_only_context=['src/test_protected.py'], allowed_edit_files=['src/feature.py'],
                batch_work_item={}, budget=edit_budget_for_profile('normal')),
        ]
        for prompt in prompts:
            self.assertIn('editable_files', prompt)
            self.assertIn('read_only_files', prompt)
            self.assertIn('tests/fixtures/check_1499.sql', prompt)
            self.assertIn(snippets[0]['text'], prompt)
            self.assertIn(reference['text'], prompt)
            self.assertIn('selected-test-evidence', prompt)
            self.assertNotIn('irrelevant-fixture-summary', prompt)
            self.assertNotIn('unseen_signature', prompt)

    def test_empty_and_default_scope_remain_ordinary_inventory(self):
        self.assertEqual(prompt_file_inventory({}, allowed_patterns=(), protected_patterns=())['editable_files'], [])
        index = {'files': [{'path': 'main.js', 'kind': 'javascript'}, {'path': 'tests/eval.py'}]}
        inventory = prompt_file_inventory(index, allowed_patterns=(), protected_patterns=())
        self.assertEqual(inventory['editable_files'], ['main.js'])
        self.assertEqual(inventory['read_only_files'], ['tests/eval.py'])
