# Evals

**Planned for phase 5 (`v0.5-evals`).** The suite will include 30+ YAML cases, deterministic
state-based graders as the primary signal, trajectory checks, a pinned LLM-as-judge for response
rubrics, N=3 repeats per case, a checked-in baseline and deterministic CI cassettes.

Metrics: task success, correct-tool rate, forbidden-action rate (must be 0), approval compliance,
duplicate-order rate (must be 0), recovery rate under faults, steps, tokens, cost and latency
p50/p95.
