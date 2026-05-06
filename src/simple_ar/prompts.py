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


def paper_note_user_prompt(paper_json: str) -> str:
    """Build the reading prompt for a single paper metadata record.

    Args:
        paper_json: JSON text containing one paper metadata object.

    Returns:
        Prompt requesting one structured paper note.
    """
    return (
        "Given one paper metadata record as JSON, write one JSON object with "
        "string fields: `paper_id`, `problem`, `method`, `limitation`, and "
        "`relevance`. Use only the supplied metadata. If the metadata is too "
        "thin, say what is unknown instead of inventing details.\n\n"
        f"Paper JSON:\n{paper_json}"
    )


def synthesize_user_prompt(notes_markdown: str, paper_notes_json: str) -> str:
    """Build the synthesis prompt from free-form and structured notes.

    Args:
        notes_markdown: Human-readable notes produced by the read stage.
        paper_notes_json: Structured notes serialized as JSON text.

    Returns:
        Prompt requesting synthesis and hypothesis Markdown as JSON fields.
    """
    return (
        "Given literature notes, write JSON with two string fields: "
        "`synthesis_markdown` and `hypothesis_markdown`. The hypothesis must be "
        "small enough for a local toy experiment.\n\n"
        f"Notes Markdown:\n{notes_markdown}\n\n"
        f"Structured Notes JSON:\n{paper_notes_json}"
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

    Returns:
        Prompt requesting a polished Markdown paper draft as JSON.
    """
    return (
        "Write JSON with one string field: `report_markdown`.\n\n"
        "The value must be a polished Markdown research report, not a run log. "
        "Use this structure exactly:\n"
        "# <paper title>\n"
        "## Abstract\n"
        "## Introduction\n"
        "## Related Work\n"
        "## Method\n"
        "## Experiments\n"
        "## Results\n"
        "## Limitations\n"
        "## Conclusion\n\n"
        "Writing style rules adapted from AutoResearchClaw:\n"
        "- Write flowing academic paragraphs. Avoid bullet lists except compact "
        "metric tables in Results.\n"
        "- The paper should read like a short workshop paper, not a technical "
        "artifact inventory.\n"
        "- Every claim about results must be supported by numbers in `results_json`.\n"
        "- Do not report p-values, confidence intervals, multiple seeds, or "
        "statistical significance unless those values appear in `results_json`.\n"
        "- Do not repeat caveats throughout the paper. Put caveats in Limitations.\n"
        "- If the literature search used fixture metadata or cache fallback, state "
        "that provenance honestly in Limitations and avoid claiming a full review.\n"
        "- If the experiment template is a tiny teaching experiment, frame the "
        "results as a reproducibility/pipeline demonstration rather than a broad "
        "scientific claim.\n"
        "- Use only citations from `papers_json`, in Pandoc style like `[@paper_id]`.\n"
        "- Never invent a citation key, paper title, arXiv id, DOI, dataset, or method.\n"
        "- Do not write a References section; the system appends it from "
        "`papers.jsonl`.\n\n"
        "Artifacts:\n\n"
        f"Topic:\n{topic}\n\n"
        f"Goal Markdown:\n{goal_markdown}\n\n"
        f"Problem Markdown:\n{problem_markdown}\n\n"
        f"Search Metadata JSON:\n{search_meta_json}\n\n"
        f"Papers JSON:\n{papers_json}\n\n"
        f"Synthesis Markdown:\n{synthesis_markdown}\n\n"
        f"Hypothesis Markdown:\n{hypothesis_markdown}\n\n"
        f"Experiment Plan JSON:\n{experiment_plan_json}\n\n"
        f"Results JSON:\n{results_json}\n"
    )
