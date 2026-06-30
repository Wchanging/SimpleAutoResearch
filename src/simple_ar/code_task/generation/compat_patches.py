from __future__ import annotations

"""Compatibility patches for generated-project scaffold mismatches.

These helpers are deliberately separated from the main generated-project repair
flow. They only address deterministic interface mismatches that appear after a
project has already been generated, such as a CLI preset name not matching the
generated config or a generated entrypoint calling an internal runner with the
wrong signature.

They must not invent benchmark conclusions or replace LLM repair. Keeping them
behind one entrypoint makes it possible to disable or downgrade scaffold
compatibility behavior for stricter benchmark runs later.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class CompatibilityPatchResult:
    applied: bool
    patch_id: str = ""
    changed_files: tuple[str, ...] = ()
    note: str = ""


def apply_generated_project_compatibility_patch(
    *,
    project_dir: Path,
    stderr_text: str,
) -> CompatibilityPatchResult:
    """Apply at most one deterministic generated-project compatibility patch."""

    for patch_id, patcher in (
        ("missing_greenfield_preset", _patch_missing_greenfield_preset),
        ("greenfield_run_experiment_call", _patch_greenfield_run_experiment_call),
        ("unexpected_keyword_argument", _patch_unexpected_keyword_argument),
    ):
        changed: list[str] = []
        if patcher(project_dir, stderr_text, changed):
            return CompatibilityPatchResult(
                applied=True,
                patch_id=patch_id,
                changed_files=tuple(changed),
                note=f"Applied generated-project compatibility patch `{patch_id}`.",
            )
    return CompatibilityPatchResult(applied=False)


def _patch_unexpected_keyword_argument(project_dir: Path, stderr_text: str, changed: list[str]) -> bool:
    match = re.search(
        r"TypeError:\s+([A-Za-z_][A-Za-z0-9_]*)\(\) got an unexpected keyword argument '([^']+)'",
        stderr_text,
    )
    if not match:
        return False
    function_name, keyword = match.group(1), match.group(2)
    for path in sorted(project_dir.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        patched = _patch_function_signature(text, function_name=function_name, keyword=keyword)
        if patched == text:
            continue
        path.write_text(patched, encoding="utf-8")
        changed.append(path.relative_to(project_dir).as_posix())
        return True
    return False


def _patch_missing_greenfield_preset(project_dir: Path, stderr_text: str, changed: list[str]) -> bool:
    matches = [
        item.strip()
        for item in re.findall(r"Unknown preset '([^']+)'", stderr_text)
        if item.strip() and "{" not in item and "}" not in item
    ]
    if not matches:
        return False
    preset_name = matches[-1]
    config_path = project_dir / "config.json"
    if not preset_name or not config_path.is_file():
        return False
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    presets = payload.get("presets")
    if not isinstance(presets, dict):
        presets = {}
        payload["presets"] = presets
    placeholders = [key for key in presets if isinstance(key, str) and "{" in key and "}" in key]
    for key in placeholders:
        if preset_name not in presets and isinstance(presets.get(key), dict):
            presets[preset_name] = presets[key]
        del presets[key]
    if preset_name not in presets:
        base = payload.get("base")
        if not isinstance(base, dict):
            base = {}
            payload["base"] = base
        presets[preset_name] = _default_greenfield_preset(base=base, requested=preset_name)
    config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    changed.append("config.json")
    return True


def _patch_greenfield_run_experiment_call(project_dir: Path, stderr_text: str, changed: list[str]) -> bool:
    should_try = (
        "run_experiment" in stderr_text
        and ("unexpected keyword argument" in stderr_text or "unhashable type: 'dict'" in stderr_text)
    )
    if not should_try:
        return False
    main_path = project_dir / "main.py"
    if not main_path.is_file():
        return False
    text = main_path.read_text(encoding="utf-8")
    replacements = {
        "run_experiment(effective_config, mode=args.mode)": "run_experiment(preset=args.preset, data_source=args.data_source)",
        "run_experiment(effective_config)": "run_experiment(preset=args.preset, data_source=args.data_source)",
    }
    patched = text
    for old, new in replacements.items():
        patched = patched.replace(old, new)
    if patched == text:
        patched = re.sub(
            r"run_experiment\(\s*effective_config\s*,\s*mode\s*=\s*args\.mode\s*\)",
            "run_experiment(preset=args.preset, data_source=args.data_source)",
            patched,
        )
    if patched == text:
        return False
    main_path.write_text(patched, encoding="utf-8")
    changed.append("main.py")
    return True


def _default_greenfield_preset(*, base: Mapping[str, Any], requested: str) -> dict[str, Any]:
    preset: dict[str, Any] = {
        "conditions": base.get("conditions", ["baseline", "candidate"]),
        "random_seed": int(base.get("random_seed", 13) or 13),
        "max_items": int(base.get("max_items", base.get("max_samples", 240)) or 240),
        "test_fraction": float(base.get("test_fraction", base.get("test_size", 0.2)) or 0.2),
        "validation_fraction": float(base.get("validation_fraction", base.get("validation_size", 0.2)) or 0.2),
        "notes": f"Auto-added by SimpleAutoResearch compatibility repair for generated preset '{requested}'.",
    }
    if requested == "smoke":
        preset["max_items"] = min(int(preset["max_items"]), 240)
    return preset


def _patch_function_signature(text: str, *, function_name: str, keyword: str) -> str:
    pattern = re.compile(
        rf"^(def\s+{re.escape(function_name)}\()([^)]*)(\)\s*(?:->\s*[^:]+)?\s*:)",
        re.MULTILINE,
    )

    def _replace(match: re.Match[str]) -> str:
        params = match.group(2).strip()
        if keyword in {part.split("=", 1)[0].split(":", 1)[0].strip().lstrip("*") for part in params.split(",")}:
            return match.group(0)
        if "**" in params:
            return match.group(0)
        if not params:
            params = f"{keyword}=None"
        else:
            params = f"{params}, {keyword}=None"
        return f"{match.group(1)}{params}{match.group(3)}"

    return pattern.sub(_replace, text, count=1)
