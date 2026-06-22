from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DependencyCandidate:
    package: str
    import_name: str
    priority: str
    role: str
    trigger_terms: tuple[str, ...]
    impact_if_missing: str
    heavy: bool = False


DEPENDENCY_CATALOG: tuple[DependencyCandidate, ...] = (
    DependencyCandidate(
        package="numpy",
        import_name="numpy",
        priority="recommended",
        role="array math, deterministic numeric baselines, and metric computation",
        trigger_terms=("machine-learning", "ml", "classification", "numeric", "feature", "metric", "array"),
        impact_if_missing="Generated code should use standard-library fallbacks for arrays and metrics.",
    ),
    DependencyCandidate(
        package="scikit-learn",
        import_name="sklearn",
        priority="recommended",
        role="packaged open datasets, classical baselines, train/test splits, and metrics",
        trigger_terms=(
            "scikit-learn",
            "sklearn",
            "load_digits",
            "load_breast_cancer",
            "load_wine",
            "open dataset",
            "classification",
            "baseline",
        ),
        impact_if_missing="Open packaged datasets and classical baselines should degrade to local/synthetic fallbacks.",
    ),
    DependencyCandidate(
        package="pandas",
        import_name="pandas",
        priority="optional",
        role="local CSV/JSONL ingestion and tabular summaries",
        trigger_terms=("csv", "jsonl", "local data", "tabular", "dataframe"),
        impact_if_missing="Generated code should parse small CSV/JSONL files with the standard library.",
    ),
    DependencyCandidate(
        package="rich",
        import_name="rich",
        priority="recommended",
        role="terminal summary tables and readable CLI output",
        trigger_terms=("rich", "terminal", "summary", "report"),
        impact_if_missing="Generated code should print plain-text summaries.",
    ),
    DependencyCandidate(
        package="pydantic",
        import_name="pydantic",
        priority="optional",
        role="typed configuration and result schema validation",
        trigger_terms=("pydantic", "configuration", "config", "schema", "validation"),
        impact_if_missing="Generated code should use dataclasses or explicit validation helpers.",
    ),
    DependencyCandidate(
        package="pytest",
        import_name="pytest",
        priority="optional",
        role="developer tests and self-check helpers",
        trigger_terms=("pytest", "test", "self-check", "quality gate"),
        impact_if_missing="Generated code should keep self-check runnable from the CLI without pytest.",
    ),
    DependencyCandidate(
        package="torch",
        import_name="torch",
        priority="optional_heavy",
        role="small neural baseline and CUDA path when available",
        trigger_terms=("torch", "pytorch", "neural", "cuda", "gpu", "mlp"),
        impact_if_missing="Generated code should use NumPy or standard-library neural-like fallbacks.",
        heavy=True,
    ),
    DependencyCandidate(
        package="requests",
        import_name="requests",
        priority="risky",
        role="remote HTTP downloads or API calls",
        trigger_terms=("download", "http", "https", "api", "network", "remote dataset", "web"),
        impact_if_missing=(
            "Prefer packaged datasets, user-provided local files, or a documented manual download path; "
            "do not make network access a hidden requirement."
        ),
    ),
)


def build_dependency_advice(task_text: str) -> dict[str, Any]:
    """Return dependency guidance for a greenfield code-task.

    The advice is intentionally observational. It checks what is importable in
    the current Python environment and suggests optional install commands, but
    it never installs packages or mutates the environment.
    """

    text = task_text.lower()
    rows: list[dict[str, Any]] = []
    for candidate in DEPENDENCY_CATALOG:
        matched_terms = [term for term in candidate.trigger_terms if term in text]
        if not matched_terms and candidate.priority not in {"recommended"}:
            continue
        installed = importlib.util.find_spec(candidate.import_name) is not None
        rows.append(
            {
                "package": candidate.package,
                "import_name": candidate.import_name,
                "priority": candidate.priority,
                "role": candidate.role,
                "status": "installed" if installed else "missing",
                "matched_terms": matched_terms,
                "impact_if_missing": candidate.impact_if_missing,
                "heavy": candidate.heavy,
            }
        )

    missing_recommended = [
        row["package"]
        for row in rows
        if row["status"] == "missing" and row["priority"] == "recommended" and not row["heavy"]
    ]
    missing_optional = [
        row["package"]
        for row in rows
        if row["status"] == "missing" and row["priority"] in {"optional", "optional_heavy"}
    ]
    missing_required = [
        row["package"]
        for row in rows
        if row["status"] == "missing" and row["priority"] == "required"
    ]
    risky = [
        row["package"]
        for row in rows
        if row["priority"] == "risky"
    ]
    installed = [row["package"] for row in rows if row["status"] == "installed"]
    install_command = (
        "uv add " + " ".join(missing_recommended)
        if missing_recommended
        else ""
    )
    pip_install_command = (
        "uv pip install " + " ".join(missing_recommended)
        if missing_recommended
        else ""
    )
    return {
        "schema_version": "code_task_dependency_advice.v1",
        "policy": "advice_only_no_auto_install",
        "installed_packages": installed,
        "missing_required": missing_required,
        "missing_recommended": missing_recommended,
        "missing_optional": missing_optional,
        "risky_packages": risky,
        "install_command": install_command,
        "ephemeral_install_command": pip_install_command,
        "notes": [
            "This advice is generated before code implementation planning.",
            "SimpleAutoResearch does not install dependencies automatically.",
            "Install missing recommended packages before rerunning execute if you want the generated project to use the stronger implementation path.",
            "Heavy optional packages such as torch are never included in the default install command.",
            "Risky packages indicate network or environment-sensitive paths; they require explicit task justification.",
        ],
        "packages": rows,
    }


def render_dependency_advice_markdown(advice: dict[str, Any]) -> str:
    lines = [
        "# Dependency Advice",
        "",
        f"- Policy: `{advice.get('policy', 'unknown')}`",
        "- Automatic install: `disabled`",
        "",
        "## Summary",
        "",
        "- Installed: " + _join_or_none(advice.get("installed_packages")),
        "- Missing required: " + _join_or_none(advice.get("missing_required")),
        "- Missing recommended: " + _join_or_none(advice.get("missing_recommended")),
        "- Missing optional: " + _join_or_none(advice.get("missing_optional")),
        "- Risky/task-sensitive: " + _join_or_none(advice.get("risky_packages")),
    ]
    if advice.get("install_command"):
        lines.extend(
            [
                "",
                "## Optional Install",
                "",
                "Persistent project dependency update:",
                "",
                f"```bash\n{advice['install_command']}\n```",
                "",
                "Current-environment install alternative:",
                "",
                f"```bash\n{advice['ephemeral_install_command']}\n```",
            ]
        )
    lines.extend(["", "## Packages", ""])
    for row in advice.get("packages", []):
        if not isinstance(row, dict):
            continue
        lines.extend(
            [
                f"### {row.get('package', '')}",
                "",
                f"- Import: `{row.get('import_name', '')}`",
                f"- Priority: `{row.get('priority', '')}`",
                f"- Status: `{row.get('status', '')}`",
                f"- Role: {row.get('role', '')}",
                f"- Impact if missing: {row.get('impact_if_missing', '')}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def dependency_advice_messages(advice: dict[str, Any]) -> tuple[str, ...]:
    installed = _join_or_none(advice.get("installed_packages"))
    missing_required = _join_or_none(advice.get("missing_required"))
    missing_recommended = _join_or_none(advice.get("missing_recommended"))
    missing_optional = _join_or_none(advice.get("missing_optional"))
    risky = _join_or_none(advice.get("risky_packages"))
    messages = [
        f"Dependency advice: installed packages detected: {installed}.",
        f"Dependency advice: missing required packages: {missing_required}.",
        f"Dependency advice: missing recommended packages: {missing_recommended}.",
        f"Dependency advice: missing optional packages: {missing_optional}.",
        f"Dependency advice: risky/task-sensitive packages: {risky}.",
    ]
    if advice.get("install_command"):
        messages.append(
            "Optional install before rerun for stronger implementation path: "
            + str(advice["install_command"])
        )
    messages.append("Dependency advice artifact: code_task/meta/dependency_advice.json")
    return tuple(messages)


def _join_or_none(value: Any) -> str:
    if isinstance(value, list) and value:
        return ", ".join(str(item) for item in value)
    if isinstance(value, tuple) and value:
        return ", ".join(str(item) for item in value)
    return "(none)"
