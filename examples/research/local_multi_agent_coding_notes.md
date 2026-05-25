# Multi-Agent Collaboration for Coding Agents Notes

Multi-agent collaboration for coding agents studies how multiple language-model
agents divide software engineering work such as repository understanding,
planning, implementation, review, testing, repair, and benchmark reporting. A
useful retrieval plan should distinguish coordination protocol, agent roles,
tool access, repository context selection, edit safety, and validation feedback.

Common method families include planner-worker-reviewer pipelines, debate or
critique loops, shared blackboard memory, task decomposition graphs, and
multi-agent repair loops that use test failures as feedback. Relevant benchmark
signals can include issue resolution rate, patch correctness, regression-test
pass rate, edit size, number of attempts, wall-clock runtime, and token cost.

Datasets and benchmark settings often involve repository-level coding tasks,
bug-fix tasks, unit-test repair, SWE-style issue resolution, or controlled
toy repositories that make evaluation affordable on local machines. Stronger
studies should report baselines such as single-agent editing, no-review
variants, no-retrieval variants, and smaller context budgets.

Reusable implementation hints include explicit work plans, bounded context
packs, protected edit scopes, attempt state tracking, patch diff review, and
failure analysis artifacts. Code links and reproduction details should be
treated as evidence only when they point to a concrete repository or script.

Important limitations include benchmark leakage, overfitting to visible tests,
coordination overhead, duplicated agent reasoning, high token cost, fragile
tool-calling traces, and unclear attribution when multiple agents contribute to
one patch. A conservative report should avoid claiming general autonomous
software engineering ability when the evidence only covers small local tasks.
