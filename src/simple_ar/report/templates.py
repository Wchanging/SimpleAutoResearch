from __future__ import annotations

from importlib import resources
from pathlib import Path

from simple_ar.report.schema import ReportRuntimeConfig, ReportTemplateBundle


class ReportTemplateError(RuntimeError):
    """Raised when a report template or criteria file cannot be loaded."""


BUILTIN_TEMPLATE_NAMES = {"survey", "survey_long", "experiment", "reproduction", "analysis_report"}


def resolve_experiment_delivery(config, analysis, decision):
    """Select a delivery structure; never equate process success with science."""
    goal = dict(analysis.get("goal_assessment") or {})
    if config.template not in {"", "auto"}:
        template, reason = config.template, "Use the explicitly requested template without changing evidence."
    elif goal.get("requested_delivery") in {"paper", "analysis_report"}:
        template = "experiment" if goal["requested_delivery"] == "paper" else "analysis_report"
        reason = "Honor the explicit delivery requirement identified in the task; preserve negative and uncertain findings."
    elif goal.get("task_type") == "reproduction":
        template, reason = "reproduction", "Explain reproduction conditions and differences; improvement is not required."
    elif (goal.get("status") == "met" and goal.get("task_type") != "unknown"
          and goal.get("evidence_refs") and analysis.get("status") == "passed"
          and decision.get("disposition") != "deliver_with_limits"):
        template, reason = "experiment", "The analysis judges the stated goal met with referenced evidence."
    else:
        template, reason = "analysis_report", "The goal is unmet or uncertain; deliver observations, limitations and continuation options."
    delivery = {"template": template, "reason": reason, "goal_assessment": goal,
                "stop_reason": decision.get("decision_reason", ""),
                "continuation_options": decision.get("continuation_options", [])}
    return config.model_copy(update={"template": template}), delivery


def load_report_template_bundle(
    *,
    report_mode: str,
    config: ReportRuntimeConfig,
    project_root: Path | None = None,
) -> ReportTemplateBundle:
    """Load the Markdown report template and reviewer criteria.

    Args:
        report_mode: Resolved report mode from the pipeline.
        config: Report runtime config.
        project_root: Repository root. Defaults to current working directory.

    Returns:
        A template bundle containing writing and review protocols.
    """
    root = project_root or Path.cwd()
    template_root = _template_root(root)
    name = _resolve_template_name(report_mode, config.template)
    template_path = _resolve_markdown_path(
        value=config.template,
        default_path=template_root / f"{name}.md",
        root=root,
    )
    criteria_path = _resolve_markdown_path(
        value=config.criteria,
        default_path=template_root / "criteria" / f"{name}_review.md",
        root=root,
        auto_values={"", "auto"},
    )
    return ReportTemplateBundle(
        name=name,
        mode=report_mode,
        template_path=str(template_path),
        criteria_path=str(criteria_path),
        template_markdown=_read_markdown(template_path),
        criteria_markdown=_read_markdown(criteria_path),
    )


def _resolve_template_name(report_mode: str, value: str) -> str:
    text = str(value or "").strip()
    if text in {"", "auto"}:
        return "experiment" if report_mode == "experiment" else "survey"
    if text in BUILTIN_TEMPLATE_NAMES:
        return text
    path = Path(text)
    if path.suffix.lower() in {".md", ".markdown"}:
        return path.stem
    return text


def _template_root(root: Path) -> Path:
    local = root / "templates" / "report"
    if local.exists():
        return local
    packaged = resources.files("simple_ar").joinpath("report_templates")
    if packaged.is_dir():
        return Path(str(packaged))
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "templates" / "report"


def _resolve_markdown_path(
    *,
    value: str,
    default_path: Path,
    root: Path,
    auto_values: set[str] | None = None,
) -> Path:
    auto = auto_values or {"", "auto"}
    text = str(value or "").strip()
    if text in auto or text in BUILTIN_TEMPLATE_NAMES:
        path = default_path
    else:
        raw = Path(text)
        path = raw if raw.is_absolute() else root / raw
    if not path.exists():
        raise ReportTemplateError(f"Report template file not found: {path}")
    if path.suffix.lower() not in {".md", ".markdown"}:
        raise ReportTemplateError(f"Report template must be Markdown: {path}")
    return path


def _read_markdown(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ReportTemplateError(f"Report template file is empty: {path}")
    return text
