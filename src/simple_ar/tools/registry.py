from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from simple_ar.tools.specs import CommonToolSpec, PermissionLevel, RiskLevel


@dataclass
class ToolRegistry:
    """In-memory registry that composes domain tools without owning their logic."""

    _specs: dict[str, CommonToolSpec] = field(default_factory=dict)

    def register(self, spec: CommonToolSpec, *, replace: bool = False) -> None:
        if spec.name in self._specs and not replace:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._specs[spec.name] = spec

    def register_many(self, specs: Iterable[CommonToolSpec], *, replace: bool = False) -> None:
        for spec in specs:
            self.register(spec, replace=replace)

    def get(self, name: str) -> CommonToolSpec | None:
        return self._specs.get(name)

    def list_specs(self, *, domain: str | None = None) -> list[CommonToolSpec]:
        specs = sorted(self._specs.values(), key=lambda item: (item.domain, item.name))
        if domain is None:
            return specs
        return [spec for spec in specs if spec.domain == domain]

    def to_json(self) -> list[dict[str, object]]:
        return [spec.model_dump(mode="json") for spec in self.list_specs()]


def default_tool_registry(
    *,
    include_report: bool = True,
    include_experiment: bool = True,
    include_code_task: bool = True,
) -> ToolRegistry:
    """Build the default registry from existing domain-level tool specs."""
    registry = ToolRegistry()
    if include_code_task:
        registry.register_many(_code_task_specs())
    if include_experiment:
        registry.register_many(_experiment_specs())
    if include_report:
        registry.register_many(_report_specs())
    return registry


def _code_task_specs() -> list[CommonToolSpec]:
    from simple_ar.code_task.tools.registry import default_code_task_tool_specs

    return default_code_task_tool_specs()


def _experiment_specs() -> list[CommonToolSpec]:
    from simple_ar.experiment.tools.registry import default_experiment_tool_specs

    specs: list[CommonToolSpec] = []
    for spec in default_experiment_tool_specs():
        specs.append(
            CommonToolSpec(
                name=spec.name,
                description=spec.description,
                input_schema=spec.input_schema,
                output_schema=spec.output_schema,
                permission_level=_permission_from_experiment(spec.permission),
                risk_level=_risk_from_permission(spec.permission),
                domain="experiment",
            )
        )
    return specs


def _report_specs() -> list[CommonToolSpec]:
    from simple_ar.report.tools import report_tool_specs

    specs: list[CommonToolSpec] = []
    for spec in report_tool_specs():
        permission = PermissionLevel.READ_ONLY if "read" in spec.permissions else PermissionLevel.PLAN
        specs.append(
            CommonToolSpec(
                name=spec.name,
                description=spec.description,
                input_schema=spec.input_schema,
                output_schema=spec.output_schema,
                permission_level=permission,
                risk_level=RiskLevel.LOW,
                domain="report",
                max_calls=spec.max_calls,
            )
        )
    return specs


def _permission_from_experiment(permission: str) -> PermissionLevel:
    if permission == "read_only":
        return PermissionLevel.READ_ONLY
    if permission == "write_patch":
        return PermissionLevel.WRITE_PATCH
    if permission == "execution":
        return PermissionLevel.EXECUTION
    if permission == "network":
        return PermissionLevel.NETWORK
    return PermissionLevel.PLAN


def _risk_from_permission(permission: str) -> RiskLevel:
    if permission == "read_only":
        return RiskLevel.LOW
    if permission == "write_patch":
        return RiskLevel.HIGH
    if permission in {"execution", "network"}:
        return RiskLevel.CRITICAL
    return RiskLevel.MEDIUM
