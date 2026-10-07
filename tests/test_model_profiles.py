"""Connection selection and compatibility; no paid requests or model fixtures."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simple_ar.integrations.llm import LLMClient, LLMError
from simple_ar.integrations.model_profiles import load_model_catalog, ModelConfigError


CATALOG = '''version = 1
[routes]
default = "writer"
code = "coder"
image = "artist"
[profiles.writer]
api = "openai_chat"
base_url = "https://writer.example/v1"
model = "writer-model"
api_key_env = "WRITER_KEY"
capabilities = ["text"]
stream = true
retry_attempts = 1
[profiles.coder]
api = "openai_chat"
base_url = "https://coder.example/v1"
model = "coder-model"
api_key_env = "CODER_KEY"
capabilities = ["text", "code"]
retry_attempts = 1
[profiles.artist]
api = "openai_images"
base_url = "https://images.example/v1"
model = "image-model"
api_key_env = "IMAGE_KEY"
capabilities = ["image_generate", "image_edit"]
'''


class ModelProfileTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.path = self.root / "models.toml"
        self.path.write_text(CATALOG, encoding="utf-8")
        self.stack.enter_context(patch.dict(os.environ, {
            "SIMPLE_AR_MODELS_CONFIG": str(self.path), "WRITER_KEY": "writer-secret",
            "CODER_KEY": "coder-secret", "OPENAI_API_KEY": "legacy-secret",
            "SIMPLE_AR_MODEL": "legacy-model"}, clear=True))
        self.stack.enter_context(patch("simple_ar.integrations.llm.load_dotenv"))
        self.stack.enter_context(patch("simple_ar.integrations.model_profiles.Path.home", return_value=self.root))

    def test_routed_requests_use_whole_connection_and_preserve_usage(self):
        parent_usage, task_usage = [], []
        parent = LLMClient.from_env(usage_callback=parent_usage.append)
        task = LLMClient.for_task(client=parent, model=parent.model, usage_callback=task_usage.append)
        response = {"choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}}
        with patch("simple_ar.integrations.llm._call_openai_sdk", return_value=response) as send:
            self.assertEqual(parent.ask("system", "write"), "ok")
            self.assertEqual(task.ask("system", "edit"), "ok")
        requests = [call.args[1] for call in send.call_args_list]
        self.assertEqual([(r["model"], r["base_url"], r["api_key"]) for r in requests], [
            ("writer-model", "https://writer.example/v1", "writer-secret"),
            ("coder-model", "https://coder.example/v1", "coder-secret")])
        self.assertTrue(requests[0]["stream"])
        self.assertEqual(len(parent_usage), 2)
        self.assertEqual(len(task_usage), 1)
        self.assertEqual(task_usage[0].model, "coder-model")
        ledger = object()
        bounded = parent.with_budget(ledger, session_id="session")
        nested = LLMClient.for_task(client=bounded)
        self.assertIs(nested._budget_ledger, ledger)
        self.assertEqual(nested._budget_session_id, "session")

    def test_named_selection_is_explicit_and_never_falls_back(self):
        self.assertEqual(LLMClient.from_env(model="profile:coder").model, "coder-model")
        self.assertEqual(LLMClient.from_env(model="route:code").model, "coder-model")
        for selector in ["profile:missing", "different-raw-model", "profile:artist"]:
            with self.subTest(selector=selector), self.assertRaises(LLMError):
                LLMClient.from_env(model=selector)
        del os.environ["CODER_KEY"]
        with self.assertRaisesRegex(LLMError, "CODER_KEY"):
            LLMClient.from_env(purpose="code")
        os.environ["SIMPLE_AR_MODELS_CONFIG"] = str(self.root / "missing")
        with self.assertRaises(LLMError):
            LLMClient.from_env()

    def test_binding_excludes_secrets_allows_key_rotation_and_detects_changes(self):
        client = LLMClient.from_env()
        binding = client.connection_binding()
        self.assertNotIn("writer-secret", json.dumps(binding))
        self.assertNotIn("writer-secret", repr(client._settings))
        os.environ["WRITER_KEY"] = "rotated"
        self.assertEqual(LLMClient.from_env().connection_binding(), binding)
        self.path.write_text(CATALOG.replace("image-model", "new-image-model"), encoding="utf-8")
        self.assertEqual(LLMClient.from_env().connection_binding(), binding)
        self.path.write_text(CATALOG.replace("coder-model", "next-model"), encoding="utf-8")
        self.assertNotEqual(LLMClient.from_env().connection_binding(), binding)

    def test_catalog_inspection_needs_no_credentials_and_images_are_not_text(self):
        catalog = load_model_catalog()
        self.assertEqual(catalog.select(purpose="image")[0], "artist")
        with self.assertRaises(ModelConfigError):
            catalog.select("profile:artist")
        from simple_ar.cli.main import main
        output = io.StringIO()
        with patch("sys.argv", ["simple-ar", "models", "--config", str(self.path)]), contextlib.redirect_stdout(output):
            main()
        self.assertIn("artist", output.getvalue())
        self.assertNotIn("writer-secret", output.getvalue())

    def test_invalid_catalog_errors_do_not_echo_input(self):
        for original, replacement in [
            ('api_key_env = "WRITER_KEY"', 'api_key = "accidental-secret"'),
            ('https://writer.example/v1', 'https://accidental-secret@writer.example/v1'),
            ('retry_attempts = 1', 'retry_attempts = 0'),
            ('api = "openai_chat"', 'api = "openai_responses"'),
        ]:
            with self.subTest(replacement=replacement):
                self.path.write_text(CATALOG.replace(original, replacement, 1), encoding="utf-8")
                with self.assertRaises(ModelConfigError) as caught:
                    load_model_catalog()
                self.assertNotIn("accidental-secret", str(caught.exception))

    def test_no_catalog_keeps_legacy_env(self):
        del os.environ["SIMPLE_AR_MODELS_CONFIG"]
        legacy = LLMClient.from_env()
        self.assertEqual(legacy.model, "legacy-model")
        self.assertEqual(legacy.connection_binding(), {})
        with self.assertRaises(LLMError):
            LLMClient.from_env(model="profile:writer")

    def test_saved_session_rejects_connection_drift_before_execution(self):
        from simple_ar.app.research_application import (
            create_session, load_session, ResearchApplicationServices, ResearchApplicationError)
        from simple_ar.research.workflow_contracts import ResearchBrief
        root = self.root / "session"
        create_session(ResearchBrief(request_text="Compare supplied evidence", requested_outputs=("summary",)),
                       root=root, services=ResearchApplicationServices(llm_client=LLMClient.from_env()))
        os.environ["WRITER_KEY"] = "rotated"
        restored = load_session(root, services=ResearchApplicationServices(llm_client=LLMClient.from_env()))
        restored.require_llm_binding()
        self.path.write_text(CATALOG.replace("writer-model", "changed-model"), encoding="utf-8")
        changed = load_session(root, services=ResearchApplicationServices(llm_client=LLMClient.from_env()))
        with self.assertRaisesRegex(ResearchApplicationError, "connections differ"):
            changed.require_llm_binding()

    def test_explicit_code_connection_survives_nested_task_clients(self):
        self.path.write_text(CATALOG.replace('code = "coder"', 'code = "writer"'), encoding="utf-8")
        chosen = LLMClient.from_env(model="profile:coder", purpose="code")
        self.assertEqual(LLMClient.for_task(client=chosen, model=chosen.model).model, "coder-model")
