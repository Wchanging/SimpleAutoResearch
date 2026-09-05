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

mkdir -p "$output_root"

{
  echo "repository_commit=$(git rev-parse HEAD)"
  if git diff --quiet && git diff --cached --quiet; then
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

echo "[1/3] Running local complete fixture smoke."
uv run --no-sync python examples/research_session_smoke.py \
  --output-root "$output_root/fixture"

if [[ "$run_online" == "1" || "$run_code_task" == "1" ]]; then
  if [[ -z "$model" ]]; then
    echo "SIMPLE_AR_MODEL must be set for an LLM-backed smoke." >&2
    exit 2
  fi
  if [[ -z "${OPENAI_API_KEY:-}" ]]; then
    echo "OPENAI_API_KEY must be set for an LLM-backed smoke." >&2
    exit 2
  fi
fi

if [[ "$run_online" == "1" ]]; then
  echo "[2/3] Running bounded online research-session smoke."
  SIMPLE_AR_LLM_RETRY_ATTEMPTS="${SIMPLE_AR_LLM_RETRY_ATTEMPTS:-1}" \
  SIMPLE_AR_LLM_TIMEOUT_SEC="${SIMPLE_AR_LLM_TIMEOUT_SEC:-90}" \
  uv run --no-sync simple-ar research-session \
    --topic "lightweight language model agents" \
    --query "large language model agents" \
    --provider arxiv \
    --max-results 1 \
    --max-chunks 5 \
    --idea-limit 1 \
    --timeout-sec 30 \
    --model "$model" \
    --report-reviewer disabled \
    --max-review-iterations 0 \
    --output-root "$output_root/online" \
    --command python -c "print('accuracy: 0.75')"
else
  echo "[2/3] Online smoke skipped (set SIMPLE_AR_RUN_ONLINE=1 to enable)."
fi

if [[ "$run_code_task" == "1" ]]; then
  echo "[3/3] Running one prepared research-to-CodeTask direction."
  SIMPLE_AR_LLM_RETRY_ATTEMPTS="${SIMPLE_AR_LLM_RETRY_ATTEMPTS:-1}" \
  SIMPLE_AR_LLM_TIMEOUT_SEC="${SIMPLE_AR_LLM_TIMEOUT_SEC:-90}" \
  uv run --no-sync simple-ar research-session \
    --topic "reliable agents" \
    --local-document examples/research_brief/fixtures/reliable_agents.md \
    --max-results 1 \
    --max-chunks 5 \
    --idea-limit 1 \
    --code-task-config examples/code_task_medium_review/configs/code_task.toml \
    --model "$model" \
    --timeout-sec "${SIMPLE_AR_EXPERIMENT_TIMEOUT_SEC:-60}" \
    --report-reviewer disabled \
    --max-review-iterations 0 \
    --output-root "$output_root/code-task"
else
  echo "[3/3] CodeTask smoke skipped (set SIMPLE_AR_RUN_CODE_TASK=1 to enable)."
fi

echo "Low-resource smoke finished. Artifacts: $output_root"
