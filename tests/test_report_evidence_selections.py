"""Short selections reduce transcription, not evidence or semantic checks."""
import copy
import json
import unittest

from simple_ar.integrations.llm import LLMResponseError
from simple_ar.report.agent import _review_section
from simple_ar.report.editor import review_document
from simple_ar.report.review_evidence import (
    resolve_review_evidence, review_evidence_locator, validate_evidence_quotes,
)
from simple_ar.report.schema import (
    ReportContext, ReportEvidenceQuote, ReportMemory, ReportRuntimeConfig,
    ReportSectionDraft, ReportSectionPlan, ReportSectionReview, ReviewerFinding,
)
from simple_ar.report.templates import load_report_template_bundle


def select(view, role, pointer):
    return next(key for key, value in view['evidence_locator']['pointers_by_role'][role].items() if value == pointer)


class EvidenceSelectionTests(unittest.TestCase):
    def view(self):
        view = {"execution_evidence": {"execution_records": [{"cwd": "/actual-copy", "duration_sec": 12.5}],
                "declared_execution_context": {"cwd": "/requested-copy"}},
            "source_evidence": [{"metadata": {"evidence_passages": [
                {"text": "  exact marginal coverage: ex-\nact; fitted ﬁeld.\n"}]}}],
            "metric_sources": {"a/b~c": 3},
            "delivery_text_observation": {"markdown_token_count": 950}}
        view["evidence_locator"] = review_evidence_locator(view)
        return view

    def resolve(self, reference, view):
        raw = {"section_id": "setup", "verdict": "warning", "findings": [{
            "finding_id": "evidence", "type": "style", "message": "A field was selected.",
            "evidence_quotes": [reference]}]}
        before = copy.deepcopy(raw)
        normalized = resolve_review_evidence(raw, view)
        self.assertEqual(raw, before)
        review = ReportSectionReview.model_validate(normalized)
        quote = review.findings[0].evidence_quotes[0]
        validate_evidence_quotes([quote], view)
        return quote, review

    def test_selection_retains_actual_path_value_and_role_without_alias_persistence(self):
        view = self.view()
        before = copy.deepcopy(view)
        for role, pointer, value in (
            ("executor_record", "/execution_evidence/execution_records/0/cwd", "/actual-copy"),
            ("declaration", "/execution_evidence/declared_execution_context/cwd", "/requested-copy"),
            ("registered_result", "/metric_sources/a~1b~0c", "3"),
            ("derived_context", "/delivery_text_observation/markdown_token_count", "950"),
        ):
            with self.subTest(role=role):
                quote, review = self.resolve({"anchor": select(view, role, pointer)}, view)
                self.assertEqual((quote.pointer, quote.quote, quote.role, quote.mode),
                    (pointer, value, role, "field_reference"))
                saved = review.model_dump(mode="json")
                self.assertNotIn('"anchor"', json.dumps(saved))
                restored = ReportSectionReview.model_validate(saved)
                validate_evidence_quotes(restored.findings[0].evidence_quotes, view)
        self.assertEqual(view, before)

    def test_pdf_selection_preserves_raw_layout_while_changed_literal_quote_stays_invalid(self):
        view = self.view()
        pointer = "/source_evidence/0/metadata/evidence_passages/0/text"
        anchor = select(view, "recorded_material", pointer)
        quote, _ = self.resolve({"anchor": anchor}, view)
        self.assertEqual(quote.quote, "  exact marginal coverage: ex-\nact; fitted ﬁeld.\n")
        for changed in ("exact; fitted field", "ex-act", "coverage: exact"):
            with self.subTest(changed=changed), self.assertRaises(LLMResponseError):
                self.resolve({"anchor": anchor, "quote": changed}, view)
        literal, _ = self.resolve({"anchor": anchor, "quote": "marginal coverage"}, view)
        self.assertEqual(literal.mode, "quotation")
        self.assertNotIn("mode", literal.model_dump(mode="json"))

    def test_direct_pointer_selects_same_original_field_without_locator_or_transcription(self):
        view = self.view()
        pointer = "/source_evidence/0/metadata/evidence_passages/0/text"
        selected, _ = self.resolve({"anchor": select(view, "recorded_material", pointer)}, view)
        view["evidence_locator"] = review_evidence_locator(view, max_entries=0)
        for reference in ({"pointer": pointer}, {"pointer": pointer, "role": "recorded_material"}):
            with self.subTest(reference=reference):
                direct, review = self.resolve(reference, view)
                self.assertEqual(direct, selected)
                restored = ReportSectionReview.model_validate(review.model_dump(mode="json"))
                validate_evidence_quotes(restored.findings[0].evidence_quotes, view)
        view.pop("evidence_locator")
        direct, _ = self.resolve({"pointer": pointer}, view)
        self.assertEqual(direct, selected)

    def test_direct_field_selection_preserves_scalars_and_declared_ownership(self):
        view = {"metric_sources": {"missing": None, "enabled": False, "delta": -0.5, "unit": ""},
            "delivery_text_observation": {"references": {"markdown": ""}},
            "objective": "Claimed condition, not an observation."}
        for pointer, value, role in (
            ("/metric_sources/missing", "null", "registered_result"),
            ("/metric_sources/enabled", "false", "registered_result"),
            ("/metric_sources/delta", "-0.5", "registered_result"),
            ("/metric_sources/unit", "", "registered_result"),
            ("/delivery_text_observation/references/markdown", "", "derived_context"),
            ("/objective", view["objective"], "declaration"),
        ):
            with self.subTest(pointer=pointer):
                quote, _ = self.resolve({"pointer": pointer}, view)
                self.assertEqual((quote.quote, quote.role, quote.mode), (value, role, "field_reference"))
                if value == "":
                    view["evidence_locator"] = review_evidence_locator(view)
                    self.assertEqual(self.resolve({"anchor": select(view, role, pointer)}, view)[0], quote)
                    restored = ReportEvidenceQuote.model_validate(quote.model_dump(mode="json"))
                    validate_evidence_quotes([restored], view)
                    with self.assertRaises(LLMResponseError):
                        self.resolve({"pointer": pointer, "quote": "", "role": role}, view)
                    with self.assertRaises(LLMResponseError):
                        validate_evidence_quotes([restored.model_copy(update={"quote": "invented"})], view)

    def test_direct_pointer_does_not_repair_a_quote_or_select_unknown_containers(self):
        view = self.view()
        pointer = "/source_evidence/0/metadata/evidence_passages/0/text"
        for reference in (
            {"pointer": pointer, "quote": "ex-act; fitted field", "role": "recorded_material"},
            {"pointer": pointer, "quote": "", "role": "recorded_material"},
            {"pointer": pointer, "role": "executor_record"},
            {"pointer": pointer, "mode": "field_reference"},
            {"pointer": "/source_evidence/0/metadata/evidence_passages"},
            {"pointer": "/source_evidence/0/metadata/evidence_passages/01/text"},
            {"pointer": "/source_evidence/1/metadata/evidence_passages/0/text"},
            {"pointer": "/evidence_locator/scope"},
            {"pointer": None}, {"pointer": 1}, {"pointer": "https://example.org/paper"},
        ):
            with self.subTest(reference=reference), self.assertRaises(LLMResponseError):
                self.resolve(reference, view)

    def test_direct_selection_stays_bound_to_original_value_on_restore(self):
        view = self.view()
        quote, review = self.resolve({"pointer": "/metric_sources/a~1b~0c"}, view)
        restored = ReportSectionReview.model_validate(review.model_dump(mode="json"))
        changed = copy.deepcopy(view)
        changed["metric_sources"]["a/b~c"] = 4
        with self.assertRaisesRegex(LLMResponseError, "absent or changed"):
            validate_evidence_quotes(restored.findings[0].evidence_quotes, changed)
        self.assertEqual(quote.quote, "3")

    def test_unknown_omitted_conflicting_and_forged_selections_are_rejected(self):
        view = self.view()
        pointer = "/execution_evidence/execution_records/0/cwd"
        anchor = select(view, "executor_record", pointer)
        for reference in ({"anchor": "executor_record:999"}, {"anchor": "executor_record:01"},
                {"anchor": "recorded_material:-1"}, {"anchor": None},
                {"anchor": anchor, "pointer": "/execution_records/0/cwd"},
                {"anchor": anchor, "role": "declaration"},
                {"anchor": anchor, "quote": "/requested-copy"},
                {"anchor": anchor, "mode": "field_reference"},
                {"pointer": pointer, "quote": "/actual-copy", "role": "executor_record", "mode": "field_reference"}):
            with self.subTest(reference=reference), self.assertRaises(LLMResponseError):
                self.resolve(reference, view)
        view["evidence_locator"] = review_evidence_locator(view, max_entries=0)
        with self.assertRaisesRegex(LLMResponseError, "not listed"):
            self.resolve({"anchor": anchor}, view)

    def test_saved_selection_does_not_rebind_to_changed_source_or_locator(self):
        view = self.view()
        pointer = "/execution_evidence/execution_records/0/cwd"
        quote, review = self.resolve({"anchor": select(view, "executor_record", pointer)}, view)
        restored = ReportSectionReview.model_validate(review.model_dump(mode="json"))
        changed = self.view()
        changed["execution_evidence"]["execution_records"][0]["cwd"] = "/other-copy"
        with self.assertRaisesRegex(LLMResponseError, "absent or changed"):
            validate_evidence_quotes(restored.findings[0].evidence_quotes, changed)
        changed = self.view()
        changed["evidence_locator"] = review_evidence_locator(changed, max_entries=0)
        validate_evidence_quotes([quote], changed)  # Saved canonical field, not a rebound alias.

    def test_partial_field_reference_cannot_weaken_exact_value_validation(self):
        view = self.view()
        pointer = "/execution_evidence/execution_records/0/duration_sec"
        quote, _ = self.resolve({"anchor": select(view, "executor_record", pointer)}, view)
        with self.assertRaisesRegex(LLMResponseError, "absent or changed"):
            validate_evidence_quotes([quote.model_copy(update={"quote": "12"})], view)
        legacy = ReportEvidenceQuote(pointer=pointer, quote="12.5", role="executor_record")
        self.assertEqual(legacy.model_dump(mode="json"), {
            "pointer": pointer, "quote": "12.5", "role": "executor_record"})

    def test_derived_field_selection_cannot_supply_scientific_counter_evidence(self):
        from simple_ar.report.review_evidence import validate_finding_anchors
        view = self.view()
        pointer = "/delivery_text_observation/markdown_token_count"
        quote, _ = self.resolve({"anchor": select(view, "derived_context", pointer)}, view)
        finding = ReviewerFinding(finding_id="mismatch", type="metric_mismatch", section_id="setup",
            severity="major", message="Not independent scientific evidence.", required_action="revise",
            draft_quotes=[{"section_id": "setup", "quote": "Reported result."}], evidence_quotes=[quote])
        review = ReportSectionReview(section_id="setup", verdict="revise_required", findings=[finding])
        with self.assertRaisesRegex(LLMResponseError, "non-derived"):
            validate_finding_anchors(review, {"setup": "Reported result."}, view)

    def test_null_boolean_and_negative_values_are_recorded_without_transcription(self):
        view = {"metric_sources": {"missing": None, "enabled": False, "delta": -0.5}}
        view["evidence_locator"] = review_evidence_locator(view)
        for key, expected in (("missing", "null"), ("enabled", "false"), ("delta", "-0.5")):
            quote, _ = self.resolve({"anchor": select(view, "registered_result", f"/metric_sources/{key}")}, view)
            self.assertEqual(quote.quote, expected)

    def test_format_correction_uses_same_minimal_selection_without_retyping_source(self):
        from simple_ar.report.agent import _review_section_with_recovery
        context = ReportContext(topic="Observed conditions", report_mode="experiment",
            results={"execution_record": {"invocation_id": "run-1", "cwd": "/actual-copy"}})
        memory = ReportMemory(section_plan=[ReportSectionPlan(section_id="setup", heading="Setup", goal="Conditions")])
        config = ReportRuntimeConfig(allow_llm_fallback=False)
        draft = ReportSectionDraft(section_id="setup", heading="Setup", draft_markdown="Recorded conditions.")
        seen = []
        class Client:
            def ask_json(self, system, prompt, **ignored):
                view = json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])[0]
                seen.append(view)
                if len(seen) == 1:
                    return {"section_id": "wrong-section", "verdict": "pass"}
                return {"section_id": "setup", "verdict": "warning", "findings": [{
                    "finding_id": "observation", "type": "style", "message": "Recorded context.",
                    "evidence_quotes": [{"anchor": select(view, "executor_record", "/execution_evidence/execution_records/0/cwd")}]}]}
        result = _review_section_with_recovery(client=Client(), context=context, memory=memory,
            section=memory.section_plan[0], draft=draft, config=config,
            template=load_report_template_bundle(report_mode=context.report_mode, config=config), label="field-recovery")
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0]["output_schema"], seen[1]["output_schema"])
        self.assertEqual(set(seen[0]["output_schema"]["findings"][0]["evidence_quotes"][0]), {"pointer"})
        reference = result.findings[0].evidence_quotes[0]
        self.assertEqual((reference.pointer, reference.quote, reference.role, reference.mode),
            ("/execution_evidence/execution_records/0/cwd", "/actual-copy", "executor_record", "field_reference"))
        restored = ReportSectionReview.model_validate(result.model_dump(mode="json"))
        validate_evidence_quotes(restored.findings[0].evidence_quotes, seen[-1])

    def test_long_list_does_not_hide_sibling_execution_observations(self):
        view = {"execution_evidence": {"execution_records": [{"role": "candidate", "invocation_id": "run-1",
            "status": "completed", "argv": [f"argument-{i}" for i in range(100)], "cwd": "/observed",
            "duration_sec": 12.5, "returncode": 0}]}}
        view["evidence_locator"] = review_evidence_locator(view, max_entries=6)
        paths = view["evidence_locator"]["pointers_by_role"]["executor_record"].values()
        for name in ("cwd", "duration_sec", "returncode"):
            self.assertIn(f"/execution_evidence/execution_records/0/{name}", paths)
        self.assertFalse(any("/argv/" in path for path in paths))
        self.assertEqual(view["evidence_locator"]["omitted_scalar_count"], 100)

    def test_listed_keys_are_explicit_and_legacy_request_arrays_still_bind_exactly(self):
        view = self.view()
        groups = view["evidence_locator"]["pointers_by_role"]
        for role, fields in groups.items():
            self.assertIsInstance(fields, dict)
            for anchor, pointer in fields.items():
                self.assertTrue(anchor.startswith(role + ":"))
                quote, _ = self.resolve({"anchor": anchor}, view)
                self.assertEqual(quote.pointer, pointer)
        legacy = copy.deepcopy(view)
        legacy["evidence_locator"]["pointers_by_role"] = {role: list(fields.values()) for role, fields in groups.items()}
        pointer = "/execution_evidence/execution_records/0/duration_sec"
        anchor = select(view, "executor_record", pointer)
        self.assertEqual(self.resolve({"anchor": anchor}, view)[0], self.resolve({"anchor": anchor}, legacy)[0])
        for request in (view, legacy):
            with self.assertRaisesRegex(LLMResponseError, "not listed"):
                self.resolve({"anchor": f"executor_record:{len(groups['executor_record'])}"}, request)

    def test_explicit_key_lookup_never_guesses_a_missing_key_from_mapping_order(self):
        view = self.view()
        pointer = "/execution_evidence/execution_records/0/duration_sec"
        view["evidence_locator"]["pointers_by_role"]["executor_record"] = {"executor_record:9": pointer}
        self.assertEqual(self.resolve({"anchor": "executor_record:9"}, view)[0].pointer, pointer)
        with self.assertRaisesRegex(LLMResponseError, "not listed"):
            self.resolve({"anchor": "executor_record:0"}, view)

    def test_actual_document_history_and_section_owners_resolve_before_persistence(self):
        context = ReportContext(topic="Observed conditions", report_mode="experiment",
            results={"execution_record": {"invocation_id": "run-1", "cwd": "/actual-copy"}})
        memory = ReportMemory(section_plan=[ReportSectionPlan(section_id="setup", heading="Setup", goal="Conditions")])
        config = ReportRuntimeConfig(max_review_iterations=1)
        template = load_report_template_bundle(report_mode="experiment", config=config)
        draft = ReportSectionDraft(section_id="setup", heading="Setup", draft_markdown="The executor used /actual-copy.")
        old = ReviewerFinding(finding_id="old", section_id="setup", type="evidence_gap", message="Check observed cwd.")
        class Client:
            def __init__(self, direct=False):
                self.direct = direct

            def ask_json(self, system, prompt, **ignored):
                view = json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])[0]
                pointer = "/execution_evidence/execution_records/0/cwd"
                reference = {"pointer": pointer} if self.direct else {"anchor": select(view, "executor_record", pointer)}
                example = (view["output_schema"]["section_reviews"][0]
                    if "section_reviews" in view["output_schema"] else view["output_schema"])
                for row in [*example.get("findings", []), *example.get("finding_checks", [])]:
                    assert set(row["evidence_quotes"][0]) == {"pointer"}
                if "section_reviews" in view["output_schema"]:
                    if view.get("historical_findings_to_check"):
                        check = view["historical_findings_to_check"][0]
                        return {"section_reviews": [{"section_id": "setup", "verdict": "pass", "finding_checks": [{
                            "finding_id": check["finding_id"], "status": "not_applicable", "explanation": "Current observed cwd matches.",
                            "draft_quotes": [draft.draft_markdown], "evidence_quotes": [reference]}]}]}
                    return {"section_reviews": [{"section_id": "setup", "verdict": "warning", "findings": [{
                        "finding_id": "obs", "type": "style", "message": "Recorded context.", "evidence_quotes": [reference]}]}]}
                return {"section_id": "setup", "verdict": "warning", "findings": [{
                    "finding_id": "obs", "type": "style", "message": "Recorded context.", "evidence_quotes": [reference]}]}
        from simple_ar.report.execution_evidence import report_execution_evidence
        for direct in (False, True):
            for prior in (None, [old]):
                rows = review_document(client=Client(direct), template=template, memory=memory, sections=[draft], config=config,
                    execution_summary={}, metric_summary={}, execution_evidence=report_execution_evidence(context), historical_findings=prior)
                refs = rows[0].finding_checks[0].evidence_quotes if prior else rows[0].findings[0].evidence_quotes
                self.assertEqual(refs[0].quote, "/actual-copy")
            result = _review_section(client=Client(direct), context=context, template=template, memory=memory,
                section=memory.section_plan[0], draft=draft, config=config, label="section-selection")
            self.assertEqual(result.findings[0].evidence_quotes[0].pointer, "/execution_evidence/execution_records/0/cwd")


if __name__ == "__main__":
    unittest.main()
