from __future__ import annotations

import importlib.metadata as importlib_metadata
import importlib.util
import re
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
        package="scipy",
        import_name="scipy",
        priority="recommended",
        role="statistical tests, optimization routines, distributions, and scientific baselines",
        trigger_terms=(
            "scipy",
            "optimization",
            "optimize",
            "statistical",
            "statistics",
            "distribution",
            "non-convex",
            "time-series",
            "time series",
            "arima",
            "sarimax",
        ),
        impact_if_missing="Generated code should use sklearn/NumPy approximations or clearly skip SciPy-specific conditions.",
    ),
    DependencyCandidate(
        package="matplotlib",
        import_name="matplotlib",
        priority="optional",
        role="diagnostic plots, benchmark figures, and saved visual summaries",
        trigger_terms=("matplotlib", "plot", "figure", "visualization", "curve", "chart"),
        impact_if_missing="Generated code should still write numeric tables and markdown summaries without plots.",
    ),
    DependencyCandidate(
        package="seaborn",
        import_name="seaborn",
        priority="optional",
        role="compact statistical visualizations built on matplotlib",
        trigger_terms=("seaborn", "heatmap", "boxplot", "statistical plot"),
        impact_if_missing="Generated code should fall back to matplotlib or omit optional styled plots.",
    ),
    DependencyCandidate(
        package="statsmodels",
        import_name="statsmodels",
        priority="optional",
        role="classical statistics and time-series models such as ARIMA/SARIMAX/ETS-style baselines",
        trigger_terms=("statsmodels", "arima", "sarimax", "ets", "theta", "time-series", "time series", "calibration"),
        impact_if_missing="Generated code should use sklearn/SciPy approximations or document that classical time-series baselines are unavailable.",
    ),
    DependencyCandidate(
        package="networkx",
        import_name="networkx",
        priority="optional",
        role="graph algorithms, causal DAG handling, and dependency graph analysis",
        trigger_terms=("networkx", "graph", "dag", "causal", "dependency graph", "call graph"),
        impact_if_missing="Generated code should represent small graphs with dictionaries/lists or skip graph-specific visualizations.",
    ),
    DependencyCandidate(
        package="imbalanced-learn",
        import_name="imblearn",
        priority="optional",
        role="SMOTE and imbalance-handling pipelines",
        trigger_terms=("imbalanced-learn", "imblearn", "smote", "oversampling", "undersampling", "class imbalance"),
        impact_if_missing="Generated code should implement bounded random/simple SMOTE-like resampling when the task permits.",
    ),
    DependencyCandidate(
        package="umap-learn",
        import_name="umap",
        priority="optional",
        role="UMAP dimensionality reduction for cluster preservation studies",
        trigger_terms=("umap", "dimensionality reduction", "manifold", "cluster structure"),
        impact_if_missing="Generated code should use PCA, t-SNE, or SpectralEmbedding as a documented fallback.",
    ),
    DependencyCandidate(
        package="hdbscan",
        import_name="hdbscan",
        priority="optional",
        role="density-based clustering baseline beyond sklearn DBSCAN",
        trigger_terms=("hdbscan", "density", "clustering", "dbscan"),
        impact_if_missing="Generated code should run DBSCAN or another sklearn-native density baseline and document the missing HDBSCAN condition.",
    ),
    DependencyCandidate(
        package="scikit-optimize",
        import_name="skopt",
        priority="optional",
        role="Bayesian optimization helpers for hyperparameter search",
        trigger_terms=("scikit-optimize", "skopt", "bayesian optimization", "bayes", "hyperparameter tuning"),
        impact_if_missing="Generated code should implement a small surrogate/acquisition loop or compare grid/random search only with a documented limitation.",
    ),
    DependencyCandidate(
        package="cma",
        import_name="cma",
        priority="optional",
        role="CMA-ES gradient-free optimization baseline",
        trigger_terms=("cma", "cma-es", "gradient-free", "non-convex", "black-box optimization"),
        impact_if_missing="Generated code should use Powell/Nelder-Mead or a bounded custom evolutionary fallback.",
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

    The advice is intentionally observational. It scans packages installed in
    the current Python environment, selects task-relevant packages dynamically,
    and uses the static catalog only as semantic hints for known libraries. It
    never installs packages or mutates the environment.
    """

    text = _normalized_text(task_text)
    installed_by_key = _scan_installed_packages()
    rows: list[dict[str, Any]] = []
    seen_packages: set[str] = set()

    for candidate in DEPENDENCY_CATALOG:
        matched_terms = _matched_terms(candidate.trigger_terms, text)
        direct_match = _mentions_any(text, (candidate.package, candidate.import_name))
        default_recommended = candidate.package in {"numpy", "scikit-learn", "rich"}
        relevant = bool(matched_terms or direct_match or default_recommended)
        if not relevant:
            continue
        installed_row = _installed_for_candidate(candidate, installed_by_key)
        if installed_row:
            rows.append(_candidate_row(candidate, installed_row, matched_terms=matched_terms))
            seen_packages.add(_package_key(str(installed_row["package"])))
        else:
            rows.append(_missing_candidate_row(candidate, matched_terms=matched_terms))
            seen_packages.add(_package_key(candidate.package))

    for package in installed_by_key.values():
        package_key = _package_key(str(package["package"]))
        if package_key in seen_packages:
            continue
        matched_terms = _package_task_matches(package, text)
        if not matched_terms:
            continue
        rows.append(_dynamic_installed_row(package, matched_terms=matched_terms))
        seen_packages.add(package_key)

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
    installed_packages = [row["package"] for row in rows if row["status"] == "installed"]
    environment_packages = list(installed_by_key.values())
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
        "environment_package_count": len(environment_packages),
        "environment_packages": environment_packages,
        "selection_policy": "dynamic_environment_scan_plus_semantic_hints",
        "installed_packages": installed_packages,
        "missing_required": missing_required,
        "missing_recommended": missing_recommended,
        "missing_optional": missing_optional,
        "risky_packages": risky,
        "install_command": install_command,
        "ephemeral_install_command": pip_install_command,
        "notes": [
            "This advice is generated before code implementation planning.",
            "SimpleAutoResearch does not install dependencies automatically.",
            "The environment package list is scanned dynamically from the active Python environment.",
            "The static dependency catalog is used only for semantic hints, not as a whitelist.",
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
        f"- Selection policy: `{advice.get('selection_policy', 'unknown')}`",
        "- Automatic install: `disabled`",
        "",
        "## Summary",
        "",
        f"- Environment packages scanned: `{advice.get('environment_package_count', 0)}`",
        "- Task-relevant installed: " + _join_or_none(advice.get("installed_packages")),
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
    lines.extend(["", "## Task-Relevant Packages", ""])
    for row in advice.get("packages", []):
        if not isinstance(row, dict):
            continue
        lines.extend(
            [
                f"### {row.get('package', '')}",
                "",
                f"- Import: `{row.get('import_name', '')}`",
                f"- Version: `{row.get('version', '')}`" if row.get("version") else "- Version: `(not installed)`",
                f"- Priority: `{row.get('priority', '')}`",
                f"- Status: `{row.get('status', '')}`",
                f"- Role: {row.get('role', '')}",
                "- Matched terms: " + _join_or_none(row.get("matched_terms")),
                f"- Impact if missing: {row.get('impact_if_missing', '')}",
                "",
            ]
        )
    lines.extend(
        [
            "",
            "## Environment Snapshot",
            "",
            "The full installed package list is stored in `dependency_advice.json` under `environment_packages`.",
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
        f"Dependency advice: environment packages scanned: {advice.get('environment_package_count', 0)}.",
        f"Dependency advice: task-relevant installed packages: {installed}.",
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


_IMPORT_NAME_OVERRIDES: dict[str, tuple[str, ...]] = {
    "beautifulsoup4": ("bs4",),
    "imbalanced-learn": ("imblearn",),
    "opencv-python": ("cv2",),
    "pillow": ("PIL",),
    "pyyaml": ("yaml",),
    "python-dateutil": ("dateutil",),
    "scikit-image": ("skimage",),
    "scikit-learn": ("sklearn",),
    "scikit-optimize": ("skopt",),
    "tree-sitter": ("tree_sitter",),
    "umap-learn": ("umap",),
}


def _scan_installed_packages() -> dict[str, dict[str, Any]]:
    packages: dict[str, dict[str, Any]] = {}
    for distribution in importlib_metadata.distributions():
        name = _distribution_name(distribution)
        if not name:
            continue
        key = _package_key(name)
        if key in packages:
            continue
        import_names = _distribution_import_names(distribution, name)
        packages[key] = {
            "package": name,
            "version": _distribution_version(distribution),
            "import_names": import_names,
            "import_name": import_names[0] if import_names else _package_to_import_name(name),
            "importable": _any_importable(import_names),
        }
    return dict(sorted(packages.items(), key=lambda item: item[0]))


def _distribution_name(distribution: importlib_metadata.Distribution) -> str:
    try:
        return str(distribution.metadata.get("Name") or "").strip()
    except Exception:
        return ""


def _distribution_version(distribution: importlib_metadata.Distribution) -> str:
    try:
        return str(distribution.version or "").strip()
    except Exception:
        return ""


def _distribution_import_names(distribution: importlib_metadata.Distribution, package_name: str) -> list[str]:
    names: list[str] = []
    override = _IMPORT_NAME_OVERRIDES.get(_package_key(package_name))
    if override:
        names.extend(override)
    try:
        top_level = distribution.read_text("top_level.txt")
    except Exception:
        top_level = None
    if top_level:
        for line in top_level.splitlines():
            item = line.strip()
            if item and _looks_like_import_name(item):
                names.append(item)
    fallback = _package_to_import_name(package_name)
    if fallback and _looks_like_import_name(fallback):
        names.append(fallback)
    return list(dict.fromkeys(names))


def _package_to_import_name(package_name: str) -> str:
    return re.sub(r"[-.]+", "_", package_name.strip())


def _looks_like_import_name(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$", value))


def _any_importable(import_names: list[str]) -> bool:
    for name in import_names[:5]:
        try:
            if importlib.util.find_spec(name) is not None:
                return True
        except (ImportError, AttributeError, ValueError):
            continue
    return False


def _installed_for_candidate(
    candidate: DependencyCandidate,
    installed: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    package_key = _package_key(candidate.package)
    if package_key in installed:
        return installed[package_key]
    import_key = _package_key(candidate.import_name)
    for row in installed.values():
        import_names = [str(item) for item in row.get("import_names", [])]
        if any(_package_key(item) == import_key for item in import_names):
            return row
    return None


def _candidate_row(
    candidate: DependencyCandidate,
    installed_row: dict[str, Any],
    *,
    matched_terms: list[str],
) -> dict[str, Any]:
    import_names = [str(item) for item in installed_row.get("import_names", []) if str(item)]
    import_name = candidate.import_name or str(installed_row.get("import_name") or "")
    if import_name and import_name not in import_names:
        import_names.insert(0, import_name)
    return {
        "package": installed_row.get("package") or candidate.package,
        "version": installed_row.get("version") or "",
        "import_name": import_name or str(installed_row.get("import_name") or ""),
        "import_names": import_names,
        "priority": candidate.priority,
        "role": candidate.role,
        "status": "installed",
        "matched_terms": matched_terms,
        "match_reason": "semantic_hint",
        "impact_if_missing": candidate.impact_if_missing,
        "heavy": candidate.heavy,
        "importable": installed_row.get("importable", False),
    }


def _missing_candidate_row(candidate: DependencyCandidate, *, matched_terms: list[str]) -> dict[str, Any]:
    return {
        "package": candidate.package,
        "version": "",
        "import_name": candidate.import_name,
        "import_names": [candidate.import_name],
        "priority": candidate.priority,
        "role": candidate.role,
        "status": "missing",
        "matched_terms": matched_terms,
        "match_reason": "semantic_hint",
        "impact_if_missing": candidate.impact_if_missing,
        "heavy": candidate.heavy,
        "importable": False,
    }


def _dynamic_installed_row(package: dict[str, Any], *, matched_terms: list[str]) -> dict[str, Any]:
    return {
        "package": package.get("package") or "",
        "version": package.get("version") or "",
        "import_name": package.get("import_name") or "",
        "import_names": package.get("import_names") or [],
        "priority": "available",
        "role": "installed package referenced by the task text",
        "status": "installed",
        "matched_terms": matched_terms,
        "match_reason": "task_mentioned_installed_package",
        "impact_if_missing": "",
        "heavy": False,
        "importable": package.get("importable", False),
    }


def _package_task_matches(package: dict[str, Any], text: str) -> list[str]:
    names = [str(package.get("package") or ""), str(package.get("import_name") or "")]
    names.extend(str(item) for item in package.get("import_names", []) if str(item))
    return [name for name in dict.fromkeys(names) if name and _term_in_text(name, text)]


def _matched_terms(terms: tuple[str, ...], text: str) -> list[str]:
    return [term for term in terms if _term_in_text(term, text)]


def _mentions_any(text: str, terms: tuple[str, ...] | list[str]) -> bool:
    return any(_term_in_text(term, text) for term in terms if term)


def _term_in_text(term: str, text: str) -> bool:
    normalized = _normalized_text(term)
    if not normalized:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(normalized) + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower().replace("_", "-")).strip()


def _package_key(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value.lower()).strip("-")
