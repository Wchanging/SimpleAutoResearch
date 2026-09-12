#!/usr/bin/env bash
set -euo pipefail

# Low-resource acceptance helper for a prepared AutoDL/Linux environment.
# It never prints or persists OPENAI_API_KEY. Network/LLM and CodeTask runs
# are opt-in so merely checking the repository does not spend API or GPU time.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

output_root="${SIMPLE_AR_AUTODL_OUTPUT_ROOT:-runs/autodl-low-resource}"
model="${SIMPLE_AR_MODEL:-}"
run_online="${SIMPLE_AR_RUN_ONLINE:-0}"
run_code_task="${SIMPLE_AR_RUN_CODE_TASK:-0}"

# Keep CPU preflight small; no CUDA workloads or provider calls by default.
export CUDA_VISIBLE_DEVICES=""
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2

mkdir -p "$output_root"

{
  echo "repository_commit=$(git rev-parse HEAD)"
  if [[ -z "$(git status --porcelain --untracked-files=normal)" ]]; then
    echo "repository_dirty=no"
  else
    echo "repository_dirty=yes"
  fi
  echo "repository_root=$repo_root"
  echo -n "python="
  python --version 2>&1
  echo -n "uv="
  uv --version 2>&1
  if command -v nvidia-smi >/dev/null 2>&1; then
    echo "gpu="
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
  else
    echo "gpu=nvidia-smi-not-found"
  fi
  echo "model_configured=$([[ -n "$model" ]] && echo yes || echo no)"
  echo "api_key_configured=$([[ -n "${OPENAI_API_KEY:-}" ]] && echo yes || echo no)"
  echo "online_requested=$run_online"
  echo "code_task_requested=$run_code_task"
} > "$output_root/environment.txt"

echo "[1/3] Running focused CPU preflight (fixture models, real processes)."
uv run --no-sync python -m unittest -v \
  tests.test_process_control \
  tests.test_research_application.ResearchApplicationTests.test_literature_report_does_not_require_or_launch_experiments \
  tests.test_research_application.ResearchApplicationTests.test_paired_experiment_recovers_baseline_and_finishes_negative_result \
  tests.test_code_task.CodeTaskTests.test_application_modifies_code_between_two_canonical_measurements \
  tests.test_code_task.CodeTaskTests.test_application_repairs_failed_candidate_without_repeating_baseline \
  tests.test_code_task.CodeTaskTests.test_application_stops_after_authorized_repair_limit \
  2>&1 | tee "$output_root/cpu-preflight.log"

if [[ "$run_online" == "1" || "$run_code_task" == "1" ]]; then
  if [[ -z "$model" ]]; then
    echo "Export SIMPLE_AR_MODEL for an LLM-backed smoke." >&2
    exit 2
  fi
  if [[ -z "${OPENAI_API_KEY:-}" ]]; then
    echo "OPENAI_API_KEY must be set for an LLM-backed smoke." >&2
    exit 2
  fi
fi

if [[ "$run_online" == "1" ]]; then
  echo "[2/3] Running new-application literature/report check (no experiment)."
  uv run --no-sync python examples/research_application_live.py \
    --session "$output_root/online" --request-timeout 90
else
  echo "[2/3] Online smoke skipped (set SIMPLE_AR_RUN_ONLINE=1 to enable)."
fi

if [[ "$run_code_task" == "1" ]]; then
  echo "[3/3] Running new-application isolated CPU baseline/CodeTask/candidate/report."
  uv run --no-sync python examples/research_application_live.py \
    --session "$output_root/code-task" --medium-review --request-timeout 90
else
  echo "[3/3] CodeTask smoke skipped (set SIMPLE_AR_RUN_CODE_TASK=1 to enable)."
fi

echo "Low-resource smoke finished. Artifacts: $output_root"
