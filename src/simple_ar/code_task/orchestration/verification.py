"""Review and validate applied repairs; experiment actions own measurement."""

from pathlib import Path
from typing import Callable

from simple_ar.code_task.execution.validation import validate_code_task
from simple_ar.code_task.review import review_code_task_changes
from simple_ar.integrations.llm import LLMClient

MessageCallback = Callable[[str], None]



def validate_repair_patch(
    run_dir: Path, *, llm_client: LLMClient | None = None, model: str | None = None,
    use_llm: bool = True, message_callback: MessageCallback | None = None,
) -> None:
    """Review and statically validate an applied repair without measuring it."""
    _emit(message_callback, "Reviewing embedded code-task repair before validation.")
    repaired_review = review_code_task_changes(
        run_dir,
        llm_client=llm_client,
        phase="post_repair",
        model=model,
        use_llm=use_llm,
        message_callback=message_callback,
    )
    if repaired_review.status == "failed":
        raise RuntimeError(
            "Embedded code-task review blocked the repair patch. "
            f"See {repaired_review.report_path}."
        )
    repaired_validation = validate_code_task(run_dir)
    if repaired_validation.status == "failed":
        raise RuntimeError(
            "Embedded code-task validation failed after repair. "
            f"See {repaired_validation.report_path}."
        )






def _emit(callback: MessageCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)
