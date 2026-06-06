from __future__ import annotations

from pathlib import Path

from simple_ar.report.schema import ReportRuntimeConfig, ReportTemplateBundle


class ReportTemplateError(RuntimeError):
    """Raised when a report template or criteria file cannot be loaded."""


BUILTIN_TEMPLATE_NAMES = {"survey", "experiment", "reproduction"}


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
