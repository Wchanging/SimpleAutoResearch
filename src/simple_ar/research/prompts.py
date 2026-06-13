from __future__ import annotations

PLAN_SYSTEM = (
    "You help scope small, reproducible research projects. "
    "Keep the plan concrete, modest, and testable."
)

READ_SYSTEM = (
    "You are a careful reading-stage reviewer. Structure retrieved paper "
    "metadata and extracted snippets into bounded notes, separating facts, "
    "uncertainty, and interpretation without inventing details."
)

SYNTHESIZE_SYSTEM = (
    "You synthesize shortlisted reading artifacts into research themes, gaps, "
    "and modest testable hypotheses grounded in cited paper/card evidence."
)

REPORT_SYSTEM = (
    "You are an academic paper author writing a concise, evidence-bound research "
    "report. Write in flowing scholarly prose rather than engineering-log style. "
    "Use only the supplied artifacts, paper ids, and metrics. Do not invent "
    "citations, datasets, statistical tests, p-values, confidence intervals, or "
    "experimental results."
)

CODE_TASK_DESIGN_SYSTEM = (
    "You translate research synthesis into a concrete, bounded code-improvement "
    "task for an existing local project. Prefer small, benchmarkable changes "
    "over broad rewrites."
)

RESEARCH_PLANNER_SYSTEM = (
    "You are a careful research retrieval planner. Break a topic into scoped "
    "research questions, expand search queries with useful terminology, and "
    "state out-of-scope boundaries. Do not claim that papers exist. Only plan "
    "what evidence should be searched for."
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


def research_planner_user_prompt(
    *,
    topic: str,
    problem_markdown: str,
    seed_queries_json: str,
    required_facets_json: str,
    max_queries: int,
    max_rounds: int,
    mode: str,
) -> str:
    """Build the prompt for LLM-backed research-question and query planning.

    Args:
        topic: User-provided research topic.
        problem_markdown: Problem artifact produced by the plan stage.
        seed_queries_json: JSON list of configured seed queries.
        required_facets_json: JSON list of facets requested by config.
        max_queries: Maximum number of executable queries to return.
        max_rounds: Planned retrieval-round budget.
        mode: Research mode, such as ``lite``, ``standard``, or ``strong``.

    Returns:
        Prompt requesting bounded JSON fields for retrieval planning.
    """
    return (
        "Create a retrieval plan for this research topic. Return JSON with "
        "these fields:\n"
        "- `questions`: list of objects with `question`, `facet`, `rationale`, "
        "`required`, `negative_scope`, and `success_criteria`.\n"
        "- `query_specs`: ordered objects capped by `max_queries`. Each object "
        "must include `facet`, `title_keywords`, `abstract_keywords`, and "
        "`rationale`.\n"
        "- `queries`: optional fallback list of short paper-search keyword "
        "queries derived from `query_specs`.\n"
        "- `required_facets`: evidence facets to cover.\n"
        "- `negative_terms`: terms or scopes that should be avoided.\n"
        "- `rationale`: one short explanation of the plan.\n\n"
        "Guidelines:\n"
        "- Include at least one overview question, then method/benchmark/"
        "dataset/code/limitation questions when relevant.\n"
        "- Expand beyond the user's exact keywords with synonyms and likely "
        "paper terminology, but keep queries specific.\n"
        "- Think like scholarly search over paper title and abstract fields. "
        "`title_keywords` should be 2-5 high-signal title terms or short "
        "phrases. `abstract_keywords` should be 3-8 supporting terms likely to "
        "appear in abstracts.\n"
        "- Query strings must be compact paper-search keyword queries suitable "
        "for OpenAlex/Semantic Scholar/arXiv/BM25, not browser questions. Prefer combinations "
        "such as `multi-agent code generation`, `LLM software engineering "
        "agents`, `agentic software engineering benchmark`, `program repair "
        "LLM agents`, or `LLM debugging unit tests`.\n"
        "- Avoid procedural words that are unlikely to be paper metadata terms "
        "unless paired with the domain, such as standalone `planner`, "
        "`implementer`, `handoff`, or `reviewer`.\n"
        "- Generate multiple focused title/abstract keyword combinations "
        "instead of one long descriptive query.\n"
        "- Do not invent paper titles, citations, datasets, or repositories.\n"
        "- Prefer queries that can help later screening and coverage checks.\n"
        "- Keep every string concise.\n\n"
        f"Topic:\n{topic}\n\n"
        f"Problem artifact:\n{problem_markdown}\n\n"
        f"Seed queries JSON:\n{seed_queries_json}\n\n"
        f"Required facets JSON:\n{required_facets_json}\n\n"
        f"Research mode: {mode}\n"
        f"max_queries: {max_queries}\n"
        f"max_rounds: {max_rounds}\n"
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
        "Given one paper metadata record as JSON, write one synthesis-ready "
        "Paper Brief as a JSON object. Use only the supplied metadata and "
        "source snippets. If evidence is thin, write `unknown` or an empty "
        "list instead of inventing details.\n\n"
        "Required fields:\n"
        "- `paper_id`: stable id from the input.\n"
        "- `title`: paper title.\n"
        "- `evidence_role`: one of overview, method, benchmark, dataset, code, "
        "limitation, comparison, or other.\n"
        "- `one_sentence_summary`: concise factual summary.\n"
        "- `problem`: problem or research setting studied by the paper.\n"
        "- `method`: method, system, or approach summary.\n"
        "- `datasets`: list of dataset or benchmark names, if visible.\n"
        "- `metrics`: list of metrics or evaluation criteria, if visible.\n"
        "- `key_claims`: list of conservative claims explicitly supported by "
        "the input.\n"
        "- `limitations`: list of limitations, risks, or missing evidence.\n"
        "- `relation_to_topic`: why this paper matters for the current topic.\n"
        "- `synthesis_hint`: one short sentence saying how synthesize should "
        "use this paper.\n"
        "- `possible_experiment_hooks`: list of small experiment/code-task "
        "ideas suggested by the evidence.\n"
        "- `open_questions`: list of questions that remain unresolved.\n"
        "- `evidence_refs`: list of paper ids, snippet labels, or empty list.\n"
        "- `confidence`: low, medium, or high.\n\n"
        "Rules:\n"
        "- Do not produce long prose. This is a machine-readable brief for "
        "later synthesis and experiment design.\n"
        "- Do not claim novelty or performance unless it is explicit in the "
        "input.\n"
        "- Prefer useful uncertainty over confident hallucination.\n\n"
        f"Paper JSON:\n{paper_json}"
        f"{evidence_block}"
    )


def read_coarse_screening_user_prompt(
    *,
    topic: str,
    problem_markdown: str,
    papers_json: str,
    research_plan_json: str,
) -> str:
    """Build the prompt for abstract-level read-stage coarse screening.

    The coarse pass is designed for larger retrieval sets. It only sees compact
    metadata and abstracts, so it should decide whether a paper deserves deeper
    reading without trying to synthesize the field.
    """
    return (
        "Coarsely screen this small batch of retrieved paper metadata for the "
        "current research problem. Return JSON with one field `decisions`, a "
        "list of objects. Each object must contain `paper_id`, `decision` "
        "(`keep` or `drop`), `coarse_relevance_score` (0-5), `reason`, "
        "`likely_facet`, and `confidence`.\n\n"
        "Rules:\n"
        "- Use only title, abstract, source metadata, and the research plan. Do "
        "not infer details that are not present.\n"
        "- This is a fast abstract-level pass. Do not write long summaries.\n"
        "- Keep papers that could help answer a research question, explain a "
        "method family, provide benchmark/dataset/code signals, or expose a "
        "limitation relevant to a later experiment.\n"
        "- Drop papers that are clearly outside the topic, purely adjacent, or "
        "lack useful evidence for the configured research questions.\n"
        "- If a paper is thin but plausibly relevant, keep it with lower "
        "confidence instead of pretending certainty.\n"
        "- `likely_facet` should be compact, such as overview, method, "
        "benchmark, dataset, code, limitation, or other.\n"
        "- Keep reasons concise and auditable.\n\n"
        f"Topic:\n{topic}\n\n"
        f"Problem artifact:\n{problem_markdown}\n\n"
        f"Research Plan JSON:\n{research_plan_json}\n\n"
        f"Paper Batch JSON:\n{papers_json}\n"
    )


def read_rerank_user_prompt(
    *,
    topic: str,
    problem_markdown: str,
    papers_json: str,
    research_plan_json: str,
    coarse_decisions_json: str,
    max_shortlist: int,
) -> str:
    """Build the prompt for read-stage reranking of coarsely kept papers."""
    return (
        "Rerank the coarsely kept papers for structured reading. Return JSON "
        "with one field `ranked_papers`, a list of objects. Each object must "
        "contain `paper_id`, `decision` (`keep` or `drop`), "
        "`reading_priority` (1 is highest priority), `relevance_score` (0-5), "
        "`quality_score` (0-5), `evidence_role`, `reason`, "
        "`synthesis_hint`, and `confidence`.\n\n"
        "Rules:\n"
        "- Prefer a diverse shortlist that covers the research questions rather "
        "than many near-duplicate papers.\n"
        "- `evidence_role` should describe how the paper helps later synthesis: "
        "overview, method, benchmark, dataset, code, limitation, comparison, or "
        "other.\n"
        "- `synthesis_hint` should be one compact sentence explaining what the "
        "synthesize stage should look for in this paper.\n"
        "- Do not invent methods, datasets, metrics, repositories, or results.\n"
        "- Keep at most `max_shortlist` papers unless the input contains fewer "
        "papers.\n"
        "- This is prioritization for reading and synthesis, not final novelty "
        "judgment.\n\n"
        f"Topic:\n{topic}\n\n"
        f"Problem artifact:\n{problem_markdown}\n\n"
        f"Research Plan JSON:\n{research_plan_json}\n\n"
        f"Coarse Decisions JSON:\n{coarse_decisions_json}\n\n"
        f"Kept Papers JSON:\n{papers_json}\n\n"
        f"max_shortlist: {max_shortlist}\n"
    )


def read_screening_user_prompt(
    *,
    topic: str,
    problem_markdown: str,
    papers_json: str,
    research_plan_json: str,
    max_shortlist: int,
) -> str:
    """Build the prompt for read-stage paper screening and prioritization."""
    return (
        "Review the retrieved paper metadata for the current research problem. "
        "Return JSON with one field `decisions`, a list of objects. Each object "
        "must contain `paper_id`, `decision` (`keep` or `drop`), "
        "`reading_priority` (1 is highest priority), `relevance_score` (0-5), "
        "`quality_score` (0-5), `reason`, and `confidence`.\n\n"
        "Rules:\n"
        "- Keep only papers that are useful for answering the research questions "
        "or designing a bounded follow-up experiment.\n"
        "- Prefer papers with methods, benchmarks, datasets, limitations, or code "
        "signals relevant to the topic.\n"
        "- Do not invent facts that are not in metadata or the research plan.\n"
        "- If metadata is thin but potentially relevant, keep it with lower "
        "confidence rather than pretending certainty.\n"
        "- Keep at most `max_shortlist` papers unless all retrieved papers are "
        "clearly necessary.\n"
        "- This is read-stage screening, not novelty review.\n\n"
        f"Topic:\n{topic}\n\n"
        f"Problem artifact:\n{problem_markdown}\n\n"
        f"Research Plan JSON:\n{research_plan_json}\n\n"
        f"Retrieved Papers JSON:\n{papers_json}\n\n"
        f"max_shortlist: {max_shortlist}\n"
    )


def synthesize_user_prompt(
    notes_markdown: str,
    paper_notes_json: str,
    evidence_snippets: str = "",
    structured_context_json: str = "",
) -> str:
    """Build the synthesis prompt from free-form and structured notes.

    Args:
        notes_markdown: Human-readable notes produced by the read stage.
        paper_notes_json: Structured notes serialized as JSON text.
        evidence_snippets: Optional source-labelled retrieval snippets from the
            current run.
        structured_context_json: Optional compact JSON assembled from Paper
            Briefs, the synthesis brief, retrieval coverage, and bounded idea
            hints.

    Returns:
        Prompt requesting synthesis and hypothesis Markdown as JSON fields.
    """
    evidence_block = _evidence_block(evidence_snippets)
    structured_block = (
        "\n\nStructured Read/Synthesis Context JSON:\n"
        f"{structured_context_json}"
        if structured_context_json.strip()
        else ""
    )
    return (
        "Given literature notes, write JSON with two string fields: "
        "`synthesis_markdown` and `hypothesis_markdown`. The hypothesis must be "
        "small enough for a local experiment or code-task follow-up. Prefer "
        "structured Paper Briefs, the synthesis brief, and source-labelled "
        "snippets when they are provided, and do not make claims that cannot "
        "be traced to notes, briefs, or snippets.\n\n"
        "Synthesis requirements:\n"
        "- Group papers into 2-4 themes or approach patterns.\n"
        "- Separate consensus, disagreement, and missing evidence.\n"
        "- Identify concrete gaps that could become bounded experiments.\n"
        "- The hypothesis should name a measurable change, likely metric, and "
        "failure condition when supported by the context.\n"
        "- Use paper ids as provenance anchors where possible.\n\n"
        f"Notes Markdown:\n{notes_markdown}\n\n"
        f"Structured Notes JSON:\n{paper_notes_json}"
        f"{structured_block}"
        f"{evidence_block}"
    )


def code_task_design_user_prompt(
    *,
    topic: str,
    goal_markdown: str,
    problem_markdown: str,
    synthesis_markdown: str,
    hypothesis_markdown: str,
    codebase_summary_json: str,
    benchmark_command: str,
    primary_metric: str,
) -> str:
    """Build the prompt that turns research artifacts into a code task.

    Args:
        topic: Original research topic.
        goal_markdown: Goal produced by the plan stage.
        problem_markdown: Problem produced by the plan stage.
        synthesis_markdown: Literature synthesis produced by the synthesize stage.
        hypothesis_markdown: Testable hypothesis produced by the synthesize stage.
        codebase_summary_json: Compact JSON summary of the target codebase.
        benchmark_command: Benchmark command that will validate the patch.
        primary_metric: Optional primary metric name.

    Returns:
        Prompt requesting a Markdown task file suitable for code-task planning.
    """
    metric = primary_metric or "the configured benchmark metrics"
    command = benchmark_command or "the configured benchmark command"
    return (
        "Write JSON with one string field: `task_markdown`.\n\n"
        "The value will be saved as `code_task/task.md` and given to a coding "
        "agent. It must be concrete enough to guide code modification without "
        "assuming the user already knows the implementation plan.\n\n"
        "Required Markdown structure:\n"
        "# Code Task\n"
        "## Objective\n"
        "## Research Motivation\n"
        "## Target Codebase Signals\n"
        "## Constraints\n"
        "## Success Criteria\n"
        "## Suggested Investigation Steps\n\n"
        "Rules:\n"
        "- Derive the task from the research artifacts and the codebase summary.\n"
        "- Keep the requested change small enough for a local benchmark run.\n"
        "- Do not instruct the agent to edit tests, benchmark files, or validation targets.\n"
        "- Mention the benchmark command and the primary metric or metric family.\n"
        "- State that the patch should preserve public APIs unless the task explicitly requires otherwise.\n"
        "- If the research artifacts are thin, write a conservative exploratory-improvement task rather than inventing paper details.\n\n"
        f"Topic:\n{topic}\n\n"
        f"Goal Markdown:\n{goal_markdown}\n\n"
        f"Problem Markdown:\n{problem_markdown}\n\n"
        f"Synthesis Markdown:\n{synthesis_markdown}\n\n"
        f"Hypothesis Markdown:\n{hypothesis_markdown}\n\n"
        f"Codebase Summary JSON:\n{codebase_summary_json}\n\n"
        f"Benchmark Command:\n{command}\n\n"
        f"Primary Metric:\n{metric}\n"
    )


def merged_code_task_design_user_prompt(
    *,
    topic: str,
    user_task_markdown: str,
    goal_markdown: str,
    problem_markdown: str,
    synthesis_markdown: str,
    hypothesis_markdown: str,
    codebase_summary_json: str,
    benchmark_command: str,
    primary_metric: str,
) -> str:
    """Build the prompt that merges a user task file with research context."""
    metric = primary_metric or "the configured benchmark metrics"
    command = benchmark_command or "the configured benchmark command"
    return (
        "Write JSON with one string field: `task_markdown`.\n\n"
        "The value will be saved as the embedded code-task `task.md`. "
        "The user's task file is the hard requirement. The research artifacts "
        "may refine motivation, constraints, target signals, and success "
        "criteria, but must not broaden the user request or override explicit "
        "user constraints.\n\n"
        "Required Markdown structure:\n"
        "# Code Task\n"
        "## Objective\n"
        "## User Requirements\n"
        "## Research-Derived Context\n"
        "## Target Codebase Signals\n"
        "## Constraints\n"
        "## Success Criteria\n"
        "## Suggested Investigation Steps\n\n"
        "Rules:\n"
        "- Preserve every explicit user requirement and protected boundary.\n"
        "- Use research context only when it helps turn the request into a "
        "clearer, testable coding task.\n"
        "- Do not instruct the agent to edit tests, benchmark files, or "
        "validation targets.\n"
        "- Mention the benchmark command and primary metric/metric family.\n"
        "- Prefer a small, reviewable patch over a broad redesign.\n"
        "- If research artifacts are thin or unrelated, say so and keep the "
        "task grounded in the user file.\n\n"
        f"Topic:\n{topic}\n\n"
        f"User Task Markdown:\n{user_task_markdown}\n\n"
        f"Goal Markdown:\n{goal_markdown}\n\n"
        f"Problem Markdown:\n{problem_markdown}\n\n"
        f"Synthesis Markdown:\n{synthesis_markdown}\n\n"
        f"Hypothesis Markdown:\n{hypothesis_markdown}\n\n"
        f"Codebase Summary JSON:\n{codebase_summary_json}\n\n"
        f"Benchmark Command:\n{command}\n\n"
        f"Primary Metric:\n{metric}\n"
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
    research_evidence_summary: str = "",
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
        research_evidence_summary: Optional compact summary built from paper,
            claim, method, dataset, code-link, and section artifacts.
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
        "## Introduction And Scope\n"
        "## Method Families\n"
        "## Evaluation And Benchmarks\n"
        "## Design Patterns And Failure Modes\n"
        "## Research Gaps And Opportunities\n"
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
        "- Do not claim an experiment was executed. Focus on method families, evaluation practices, design patterns, failure modes, gaps, and limitations.\n"
        "- Do not include operational sections such as Search Scope, Evidence Summary, Pipeline, Artifacts, or Stage Outputs.\n"
        "- Method Families should build a taxonomy or comparison table from technical roles, assumptions, and validation mechanisms; do not list papers as a retrieval log.\n"
        "- Evaluation And Benchmarks should compare evidence strength, tasks, metrics, and costs only when supported by the supplied metadata.\n"
        "- Each strong conclusion should include a boundary condition, such as task scale, benchmark type, cost, or repository-level transfer risk.\n"
        "- Research Gaps And Opportunities should list concrete gaps or next-step experiment ideas, not conclusions from nonexistent experiments.\n"
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
        "- When `Research Evidence Summary` is provided, use it to structure "
        "method families, evaluation patterns, design patterns, gaps, and limitations. Do not "
        "invent evidence beyond those cards.\n"
        "- Remove prompt-planning residue from the final prose: do not write `Hint`, "
        "`Use this paper as`, `Paper Brief`, or `Additional synthesis detail`.\n"
        "- Prefer cross-paper synthesis and comparison. Avoid isolated paragraphs "
        "about one paper unless it is introduced as a milestone or contrast point.\n"
        "- Do not repeat caveats throughout the paper. Put caveats in Limitations.\n"
        "- If the literature search used fixture metadata or cache fallback, state "
        "that provenance honestly in Limitations and avoid claiming a full review.\n"
        "- If a paper row has source `fixture`, treat it as placeholder metadata "
        "only. Do not describe fixture rows as real prior work, do not say they "
        "studied the topic, and do not use them as scientific support beyond "
        "provenance disclosure.\n"
        "- When fixture metadata is the only literature source, keep literature claims "
        "to provenance wording such as placeholder metadata; do "
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
        f"{_research_evidence_block(research_evidence_summary)}"
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


def _research_evidence_block(summary: str) -> str:
    """Format compact research evidence cards for report drafting."""
    stripped = summary.strip()
    if not stripped:
        return ""
    return (
        "\n\nResearch Evidence Summary:\n"
        "This summary is generated from structured search-stage evidence artifacts.\n"
        f"{stripped}"
    )
