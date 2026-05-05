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
    return (
        "Given this research topic, write JSON with two string fields: "
        "`goal_markdown` and `problem_markdown`.\n\n"
        f"Topic:\n{topic}"
    )


def read_user_prompt(papers_json: str) -> str:
    return (
        "Given paper metadata as JSON, write JSON with two fields: "
        "`notes_markdown` as Markdown and `paper_notes` as a list of objects. "
        "Each note object should include paper_id, problem, method, limitation, "
        "and relevance.\n\n"
        f"Papers JSON:\n{papers_json}"
    )


def synthesize_user_prompt(notes_markdown: str, paper_notes_json: str) -> str:
    return (
        "Given literature notes, write JSON with two string fields: "
        "`synthesis_markdown` and `hypothesis_markdown`. The hypothesis must be "
        "small enough for a local toy experiment.\n\n"
        f"Notes Markdown:\n{notes_markdown}\n\n"
        f"Structured Notes JSON:\n{paper_notes_json}"
    )
