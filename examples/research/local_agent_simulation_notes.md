# Agent Simulation Evaluation Notes

Agent simulation research often compares agent behavior across repeated runs,
ablation settings, and scenario variants. A useful survey should distinguish
between environment design, agent policy, interaction protocol, evaluation
metric, and reproducibility constraints.

For lightweight local experiments, practical metrics can include success rate,
mean episode reward, coordination failures, runtime cost, and variance across
random seeds. A good report should avoid claiming generality when evidence only
covers a small synthetic setup.

Common implementation risks include hidden state leakage between episodes,
too few random seeds, missing baseline agents, and evaluation scripts that
measure only final success while ignoring instability or resource use.
