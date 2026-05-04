from __future__ import annotations

from enum import IntEnum


class Stage(IntEnum):
    PLAN = 1
    SEARCH = 2
    READ = 3
    SYNTHESIZE = 4
    DESIGN = 5
    CODE = 6
    RUN = 7
    REPORT = 8


STAGE_SEQUENCE: tuple[Stage, ...] = tuple(Stage)

STAGE_SLUGS: dict[Stage, str] = {
    Stage.PLAN: "plan",
    Stage.SEARCH: "search",
    Stage.READ: "read",
    Stage.SYNTHESIZE: "synthesize",
    Stage.DESIGN: "design",
    Stage.CODE: "code",
    Stage.RUN: "run",
    Stage.REPORT: "report",
}


def stage_dir_name(stage: Stage) -> str:
    return f"{int(stage):02d}-{STAGE_SLUGS[stage]}"


def parse_stage(value: str | int | Stage) -> Stage:
    if isinstance(value, Stage):
        return value
    if isinstance(value, int):
        return Stage(value)

    normalized = str(value).strip().lower().replace("_", "-")
    if normalized.isdigit():
        return Stage(int(normalized))

    for stage, slug in STAGE_SLUGS.items():
        if normalized in {slug, stage.name.lower().replace("_", "-")}:
            return stage

    allowed = ", ".join(STAGE_SLUGS.values())
    raise ValueError(f"Unknown stage {value!r}. Expected one of: {allowed}")


def stage_range(from_stage: Stage, to_stage: Stage) -> tuple[Stage, ...]:
    if int(from_stage) > int(to_stage):
        raise ValueError(
            f"from_stage must be <= to_stage, got {from_stage.name} > {to_stage.name}"
        )
    return tuple(
        stage for stage in STAGE_SEQUENCE if int(from_stage) <= int(stage) <= int(to_stage)
    )
