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
