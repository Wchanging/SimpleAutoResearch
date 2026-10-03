"""Current report notes follow adopted drafts; history is not another input."""
import copy
import json
import unittest

from simple_ar.report.agent import run_report_agent
from simple_ar.report.schema import ReportContext, ReportMemory, ReportRuntimeConfig, ReportSectionPlan
from simple_ar.report.templates import load_report_template_bundle
from simple_ar.report.tool_gateway import ReportToolGateway
from report_review_fixtures import draft_quotes


class CurrentReportNotesTests(unittest.TestCase):
    def inputs(self, *, document_review=False, reviewer="llm", count=2):
        context = ReportContext(topic="Evidence boundaries", report_mode="supplied_materials")
        memory = ReportMemory(limitations=["Declared input constraint."],
            open_questions=["Unanswered input question."], section_plan=[
                ReportSectionPlan(section_id=sid, heading=sid, goal="Explain bounded observations",
                    draft_order=index, final_order=index)
                for index, sid in enumerate(("scope", "conclusion")[:count], start=1)])
        config = ReportRuntimeConfig(document_review=document_review, reviewer=reviewer,
            max_review_iterations=1, allow_llm_fallback=False)
        return dict(context=context, memory=memory, config=config,
            template=load_report_template_bundle(report_mode=context.report_mode, config=config))

    def test_section_revision_replaces_notes_before_the_next_writer(self):
        kwargs = self.inputs()
        saved, views = [], {}

        class Client:
            def ask_json(self, system, prompt, *, label="", **unused):
                view = json.JSONDecoder().raw_decode(prompt[prompt.index("{"):])[0]
                views[label] = view
                sid = view["section"]["section_id"]
                if "reviewer" in label:
                    if sid == "scope" and "round-2" not in label:
                        return {"verdict": "revise_required", "revision_instructions": ["Qualify the account."],
                            "findings": [{"finding_id": "scope", "type": "style", "severity": "major",
                                "required_action": "revise", "section_id": sid, "message": "Qualify the account.",
                                "draft_quotes": draft_quotes(prompt, sid)}]}
                    return {"verdict": "pass"}
                revised = "reviser" in label
                return {"section_id": sid, "heading": sid, "draft_markdown": "Only supplied observations are described.",
                    "limitations": (["Current section qualification."] if revised else ["Superseded model assertion."])
                        if sid == "scope" else [],
                    "open_questions": (["Current section question."] if revised else ["Superseded model question."])
                        if sid == "scope" else []}

        result = run_report_agent(**kwargs, client=Client(), gateway=ReportToolGateway(kwargs["context"]),
            checkpoint_sink=saved.append)
        self.assertEqual(views["report-writer-conclusion"]["limitations"],
            ["Declared input constraint.", "Current section qualification."])
        # The original scope draft remains in history, not the current note view.
        self.assertIn("Superseded model assertion.", saved[-1]["iterations"][0]["draft"]["limitations"])
        self.assertEqual(result.memory.open_questions, ["Unanswered input question.", "Current section question."])
        self.assertNotIn("Superseded model assertion.", result.memory.limitations)
        self.assertEqual(kwargs["memory"].limitations, ["Declared input constraint."])

    def test_completed_legacy_resume_drops_only_notes_with_recorded_draft_owners(self):
        kwargs = self.inputs(reviewer="disabled", count=1)
        saved = []

        class OriginalClient:
            def ask_json(self, *args, **unused):
                return {"section_id": "scope", "heading": "Scope", "draft_markdown": "Original.",
                    "limitations": ["Old assertion."], "open_questions": ["Old question."]}

        run_report_agent(**kwargs, client=OriginalClient(), gateway=ReportToolGateway(kwargs["context"]),
            checkpoint_sink=saved.append)
        checkpoint = copy.deepcopy(saved[-1])
        checkpoint["sections"][0].update(draft_markdown="Replaced.", limitations=[], open_questions=[])
        checkpoint["memory"]["limitations"].append("Legacy note with unknown owner.")
        checkpoint["memory"]["open_questions"].append("Legacy question with unknown owner.")
        rejected = copy.deepcopy(checkpoint["iterations"][0])
        rejected.update(action="document_revise", adopted=False)
        rejected["draft"].update(limitations=["Rejected candidate note."], open_questions=["Rejected question."])
        checkpoint["iterations"].append(rejected)
        checkpoint["memory"]["limitations"].append("Rejected candidate note.")
        checkpoint["memory"]["open_questions"].append("Rejected question.")
        before = copy.deepcopy(checkpoint)

        class NoCalls:
            def ask_json(self, *args, **unused):
                raise AssertionError("Completed restore cannot spend another model call")

        result = run_report_agent(**kwargs, client=NoCalls(), gateway=ReportToolGateway(kwargs["context"]),
            completed_checkpoint=checkpoint)
        self.assertEqual(result.memory.limitations,
            ["Declared input constraint.", "Legacy note with unknown owner."])
        self.assertEqual(result.memory.open_questions,
            ["Unanswered input question.", "Legacy question with unknown owner."])
        self.assertEqual(checkpoint, before)

        # A literal shared with a deleted draft cannot erase an original constraint.
        kwargs["memory"].limitations.append("Old assertion.")
        again = run_report_agent(**kwargs, client=NoCalls(), gateway=ReportToolGateway(kwargs["context"]),
            completed_checkpoint=checkpoint)
        self.assertIn("Old assertion.", again.memory.limitations)

    def test_pending_draft_is_not_adopted_and_cannot_pollute_resumed_review(self):
        kwargs = self.inputs(count=1)
        saved = []

        class Client:
            def ask_json(self, *args, label="", **unused):
                if "reviewer" in label:
                    return {"verdict": "pass"}
                return {"section_id": "scope", "heading": "Scope", "draft_markdown": "Candidate.",
                    "limitations": ["Pending candidate note."], "open_questions": ["Pending question."]}

        def interrupt(row):
            saved.append(row)
            if row["pending_draft"] is not None:
                raise RuntimeError("Stopped before review")

        with self.assertRaisesRegex(RuntimeError, "before review"):
            run_report_agent(**kwargs, client=Client(), gateway=ReportToolGateway(kwargs["context"]),
                checkpoint_sink=interrupt)
        checkpoint = copy.deepcopy(saved[-1])
        checkpoint["memory"]["limitations"].append("Pending candidate note.")
        checkpoint["memory"]["open_questions"].append("Pending question.")
        test = self

        class Resumed:
            def ask_json(self, system, prompt, *, label="", **unused):
                test.assertIn("reviewer", label)
                view = json.JSONDecoder().raw_decode(prompt[prompt.index("{"):])[0]
                test.assertEqual(view["known_limitations"], ["Declared input constraint."])
                return {"verdict": "pass"}

        result = run_report_agent(**kwargs, client=Resumed(), gateway=ReportToolGateway(kwargs["context"]),
            completed_checkpoint=checkpoint)
        self.assertIn("Pending candidate note.", result.memory.limitations)
        self.assertIn("Pending question.", result.memory.open_questions)

    def test_document_adoption_updates_notes_before_immediate_whole_document_recheck(self):
        kwargs = self.inputs(document_review=True)
        views, saved = {}, []

        class Client:
            def ask_json(self, system, prompt, *, label="", **unused):
                view = json.JSONDecoder().raw_decode(prompt[prompt.index("{"):])[0]
                views[label] = view
                if label == "report-document-reviewer":
                    return {"section_reviews": [{"section_id": "scope", "verdict": "revise_required",
                        "revision_instructions": ["Qualify this paragraph."], "findings": [{
                            "finding_id": "qualify", "type": "style", "severity": "major", "required_action": "revise",
                            "section_id": "scope", "message": "Qualify this paragraph.",
                            "draft_quotes": draft_quotes(prompt, "scope")}]}]}
                if label == "report-document-verifier":
                    return {"section_reviews": []}
                if "verifier" in label or "reviewer" in label:
                    return {"verdict": "pass"}
                sid = view["section"]["section_id"]
                revised = "document-reviser" in label
                return {"section_id": sid, "heading": sid, "draft_markdown": "Current bounded account.",
                    "limitations": ["New scope note."] if revised else (["Old scope note."] if sid == "scope" else []),
                    "open_questions": ["New scope question."] if revised else (["Old scope question."] if sid == "scope" else [])}

        result = run_report_agent(**kwargs, client=Client(), gateway=ReportToolGateway(kwargs["context"]),
            checkpoint_sink=saved.append)
        self.assertEqual(views["report-document-verifier"]["known_limitations"],
            ["Declared input constraint.", "New scope note."])
        self.assertEqual(result.memory.open_questions, ["Unanswered input question.", "New scope question."])
        self.assertTrue(any(row["action"] == "document_revise" and row["adopted"] for row in saved[-1]["iterations"]))
        self.assertEqual(result.memory.model_dump(mode="json"), saved[-1]["memory"])


    def test_shared_current_notes_survive_and_projection_does_not_change_history(self):
        from simple_ar.report.narrative import adopted_memory_notes
        from simple_ar.report.schema import ReportSectionDraft, ReportIterationRecord
        initial = ReportMemory(limitations=["Input boundary."], open_questions=["Input unknown."])
        old = ReportSectionDraft(section_id="scope", heading="Scope", limitations=["Shared restriction."],
            open_questions=["Shared question."])
        replacement = old.model_copy(update={"limitations": [], "open_questions": []})
        other = old.model_copy(update={"section_id": "other"})
        recorded = ReportMemory(limitations=["Input boundary.", "Shared restriction."],
            open_questions=["Input unknown.", "Shared question."])
        history = [ReportIterationRecord(iteration=1, section_id="scope", action="draft", status="drafted", draft=old)]
        before = [row.model_dump(mode="json") for row in (initial, recorded, old, replacement, other, *history)]
        self.assertEqual(adopted_memory_notes(initial, recorded, [replacement, other], history),
            {"limitations": ["Input boundary.", "Shared restriction."],
             "open_questions": ["Input unknown.", "Shared question."]})
        self.assertEqual(adopted_memory_notes(initial, recorded, [replacement], history),
            {"limitations": ["Input boundary."], "open_questions": ["Input unknown."]})
        self.assertEqual([row.model_dump(mode="json") for row in (initial, recorded, old, replacement, other, *history)], before)


if __name__ == "__main__":
    unittest.main()
