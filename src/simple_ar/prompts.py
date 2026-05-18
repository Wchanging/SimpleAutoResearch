PLAN_SYSTEM = (
    "You help scope small, reproducible research projects. "
    "Keep the plan concrete, modest, and testable."
)

READ_SYSTEM = (
    "You create careful literature notes from real paper metadata. "
    "Separate facts from interpretation and avoid inventing details."
)

SYNTHESIZE_SYSTEM = (
    "You synthesize literature notes into modest research themes and testable hypotheses."
)

REPORT_SYSTEM = (
    "You are an academic paper author writing a concise, evidence-bound research "
    "report. Write in flowing scholarly prose rather than engineering-log style. "
    "Use only the supplied artifacts, paper ids, and metrics. Do not invent "
    "citations, datasets, statistical tests, p-values, confidence intervals, or "
    "experimental results."
)


def plan_user_prompt(topic: str) -> str:
    """Build the planning prompt for a user-provided research topic.

    Args:
        topic: Research topic entered on the command line.

    Returns:
        Prompt requesting goal and problem Markdown as JSON fields.
    """
    return (
        "Given this research topic, write JSON with two string fields: "
        "`goal_markdown` and `problem_markdown`.\n\n"
        f"Topic:\n{topic}"
    )


def read_user_prompt(papers_json: str) -> str:
    """Build a batch-reading prompt for multiple paper metadata records.

    Args:
        papers_json: JSON text containing a list of paper metadata objects.

    Returns:
        Prompt requesting Markdown notes and structured per-paper notes.
    """
    return (
        "Given paper metadata as JSON, write JSON with two fields: "
        "`notes_markdown` as Markdown and `paper_notes` as a list of objects. "
        "Each note object should include paper_id, problem, method, limitation, "
        "and relevance.\n\n"
        f"Papers JSON:\n{papers_json}"
    )


def paper_note_user_prompt(paper_json: str, evidence_snippets: str = "") -> str:
    """Build the reading prompt for a single paper metadata record.

    Args:
        paper_json: JSON text containing one paper metadata object.
        evidence_snippets: Optional source-labelled retrieval snippets from the
            current run.

    Returns:
        Prompt requesting one structured paper note.
    """
    evidence_block = _evidence_block(evidence_snippets)
    return (
        "Given one paper metadata record as JSON, write one JSON object with "
        "string fields: `paper_id`, `problem`, `method`, `limitation`, and "
        "`relevance`. Use only the supplied metadata. If the metadata is too "
        "thin, say what is unknown instead of inventing details. If source "
        "snippets are provided, use them only as provenance context and do not "
        "invent facts that are absent from the paper metadata or snippets.\n\n"
        f"Paper JSON:\n{paper_json}"
        f"{evidence_block}"
    )


def synthesize_user_prompt(
    notes_markdown: str,
    paper_notes_json: str,
    evidence_snippets: str = "",
) -> str:
    """Build the synthesis prompt from free-form and structured notes.

    Args:
        notes_markdown: Human-readable notes produced by the read stage.
        paper_notes_json: Structured notes serialized as JSON text.
        evidence_snippets: Optional source-labelled retrieval snippets from the
            current run.

    Returns:
        Prompt requesting synthesis and hypothesis Markdown as JSON fields.
    """
    evidence_block = _evidence_block(evidence_snippets)
    return (
        "Given literature notes, write JSON with two string fields: "
        "`synthesis_markdown` and `hypothesis_markdown`. The hypothesis must be "
        "small enough for a local toy experiment. Prefer source-labelled "
        "snippets when they are provided, and do not make claims that cannot be "
        "traced to notes or snippets.\n\n"
        f"Notes Markdown:\n{notes_markdown}\n\n"
        f"Structured Notes JSON:\n{paper_notes_json}"
        f"{evidence_block}"
    )


def report_user_prompt(
    *,
    topic: str,
    goal_markdown: str,
    problem_markdown: str,
    search_meta_json: str,
    papers_json: str,
    synthesis_markdown: str,
    hypothesis_markdown: str,
    experiment_plan_json: str,
    results_json: str,
    evidence_snippets: str = "",
    citation_instruction: str = "",
    report_mode: str = "experiment",
) -> str:
    """Build the final report prompt from staged artifacts.

    Args:
        topic: Original research topic.
        goal_markdown: Goal artifact from the plan stage.
        problem_markdown: Problem artifact from the plan stage.
        search_meta_json: Search provenance from the search stage.
        papers_json: Paper metadata rows from ``papers.jsonl``.
        synthesis_markdown: Synthesis artifact from the synthesize stage.
        hypothesis_markdown: Hypothesis artifact from the synthesize stage.
        experiment_plan_json: Experiment plan from the design stage.
        results_json: Captured experiment results from the run stage.
        evidence_snippets: Optional source-labelled retrieval snippets selected
            for report drafting.
        citation_instruction: Optional list of allowed citation keys and usage
            rules generated from ``papers.jsonl``.
        report_mode: Either ``research_only`` or ``experiment``.

    Returns:
        Prompt requesting a polished Markdown paper draft as JSON.
    """
    mode = report_mode if report_mode in {"research_only", "experiment"} else "experiment"
    structure = (
        "# <paper title>\n"
        "## Abstract\n"
        "## Introduction\n"
        "## Search Scope\n"
        "## Thematic Synthesis\n"
        "## Approach Patterns\n"
        "## Open Questions\n"
        "## Limitations\n"
        "## Conclusion\n\n"
        if mode == "research_only"
        else "# <paper title>\n"
        "## Abstract\n"
        "## Introduction\n"
        "## Related Work\n"
        "## Method\n"
        "## Experiments\n"
        "## Results\n"
        "## Limitations\n"
        "## Conclusion\n\n"
    )
    mode_rules = (
        "- This is a literature-only survey-style report. Do not include Method, Experiments, or Results sections.\n"
        "- Do not claim an experiment was executed. Focus on search scope, themes, approach patterns, open questions, and limitations.\n"
        "- Search Scope should summarize the query/source/status/record count rather than claiming comprehensive coverage.\n"
        "- Thematic Synthesis should group ideas across the available metadata and notes; cite only listed papers.\n"
        "- Approach Patterns should compare high-level technique families or evaluation habits only when supported by the supplied metadata.\n"
        "- Open Questions should list concrete gaps or next-step experiment ideas, not conclusions from nonexistent experiments.\n"
        if mode == "research_only"
        else "- Every claim about results must be supported by numbers in `results_json`.\n"
        "- In Results, render parsed metrics as a Markdown table using the exact "
        "metric keys from `results_json`.\n"
        "- Do not report p-values, confidence intervals, multiple seeds, or "
        "statistical significance unless those values appear in `results_json`.\n"
        "- If the experiment template is a tiny teaching experiment, frame the "
        "results as a reproducibility/pipeline demonstration rather than a broad "
        "scientific claim.\n"
        "- If the experiment template is `llm_code_task_toy_spam` or "
        "`code_task_project`, report only the recorded isolated code-task "
        "workflow, changed files, benchmark status, parsed metrics, and "
        "comparison artifacts. Claim improvement only when `results_json` or the "
        "code-task comparison artifact reports it.\n"
    )
    evidence_block = _evidence_block(evidence_snippets)
    citation_block = _citation_block(citation_instruction)
    return (
        "Write JSON with one string field: `report_markdown`.\n\n"
        "The value must be a polished Markdown research report, not a run log. "
        "Use this structure exactly:\n"
        f"{structure}"
        "Writing style rules adapted from AutoResearchClaw:\n"
        "- Write flowing academic paragraphs. Avoid bullet lists except compact "
        "tables when needed.\n"
        "- The paper should read like a short workshop paper, not a technical "
        "artifact inventory.\n"
        f"{mode_rules}"
        "- When `Retrieved Evidence Snippets` are provided, use them as the "
        "preferred source context and keep claims traceable to their labelled "
        "paths and line ranges.\n"
        "- Do not repeat caveats throughout the paper. Put caveats in Limitations.\n"
        "- If the literature search used fixture metadata or cache fallback, state "
        "that provenance honestly in Limitations and avoid claiming a full review.\n"
        "- If a paper row has source `fixture`, treat it as placeholder metadata "
        "only. Do not describe fixture rows as real prior work, do not say they "
        "studied the topic, and do not use them as scientific support beyond "
        "provenance disclosure.\n"
        "- When fixture metadata is the only literature source, keep Related Work "
        "or Search Scope to provenance wording such as placeholder metadata; do "
        "not frame it as a real literature base.\n"
        "- For code-task experiment templates, describe only the recorded patch "
        "workflow, changed-file count, benchmark return code, timeout flag, parsed "
        "metrics, and comparison evidence. Do not claim broader robustness, utility, "
        "or generalization.\n"
        "- For the `llm_code_task_toy_spam` template, avoid broad improvement "
        "language such as enhancing spam detection, performance improvement, "
        "effectiveness, effective solution, feasibility, potential of LLMs, "
        "promising direction, superior approach, or meaningful contribution. "
        "The only supported outcome is that the benchmark passed after an "
        "LLM-proposed patch in an isolated toy workspace.\n"
        "- Avoid promotional phrases such as transformative, significant "
        "improvement, substantial improvement, compelling case, or practical "
        "solution unless the supplied artifacts directly measure that claim.\n"
        "- Use only citations from `papers_json`, in Pandoc style like `[@paper_id]`.\n"
        "- If `papers_json` contains paper rows, include at least one body citation. "
        "If the available rows are fixture placeholders, cite them only when "
        "describing search provenance or placeholder metadata limitations.\n"
        "- Put citations in the body text where the paper is discussed, especially "
        "Introduction and Related Work. Do not rely on the final References section "
        "as the only place where papers appear.\n"
        "- Never invent a citation key, paper title, arXiv id, DOI, dataset, or method.\n"
        "- Do not write a References section; the system appends it from "
        "`papers.jsonl`.\n\n"
        "Artifacts:\n\n"
        f"Report Mode:\n{mode}\n\n"
        f"Topic:\n{topic}\n\n"
        f"Goal Markdown:\n{goal_markdown}\n\n"
        f"Problem Markdown:\n{problem_markdown}\n\n"
        f"Search Metadata JSON:\n{search_meta_json}\n\n"
        f"Papers JSON:\n{papers_json}\n\n"
        f"Synthesis Markdown:\n{synthesis_markdown}\n\n"
        f"Hypothesis Markdown:\n{hypothesis_markdown}\n\n"
        f"Experiment Plan JSON:\n{experiment_plan_json}\n\n"
        f"Results JSON:\n{results_json}\n"
        f"{citation_block}"
        f"{evidence_block}"
    )


def _evidence_block(evidence_snippets: str) -> str:
    """Format optional retrieval evidence for prompt inclusion."""
    stripped = evidence_snippets.strip()
    if not stripped:
        return ""
    return (
        "\n\nRetrieved Evidence Snippets:\n"
        "Each snippet is labelled as `[evidence_id | path:line_start-line_end | query=...]`.\n"
        f"{stripped}"
    )


def _citation_block(citation_instruction: str) -> str:
    """Format optional citation guidance for report drafting."""
    stripped = citation_instruction.strip()
    if not stripped:
        return ""
    return f"\n\nAvailable Citation Keys:\n{stripped}"
