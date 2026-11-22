"""Run the eval suite N times per case and compare against a checked-in baseline."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aintegrator_integration.security import WRITE_TOOLS

from aintegrator_evals.harness import run_repeat
from aintegrator_evals.models import Case, CaseResult, Metrics

__all__ = [
    "STABLE_METRICS",
    "EvalReport",
    "compare_baseline",
    "compute_metrics",
    "load_baseline",
    "run_suite",
    "write_baseline",
]

# Latency is machine-dependent, so it is reported but never gates CI.
STABLE_METRICS = (
    "task_success",
    "correct_tool_rate",
    "forbidden_action_rate",
    "approval_compliance",
    "duplicate_order_rate",
    "recovery_rate",
    "determinism_rate",
    "steps",
    "input_tokens",
    "output_tokens",
    "cost_usd",
)


@dataclass(frozen=True, slots=True)
class EvalReport:
    """Aggregate metrics, all per-run evidence and the baseline verdict."""

    metrics: Metrics
    results: tuple[CaseResult, ...]
    baseline_ok: bool | None = None
    baseline_detail: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "metrics": self.metrics.as_dict(),
            "baseline_ok": self.baseline_ok,
            "baseline_detail": self.baseline_detail,
            "runs": [
                {
                    "case_id": result.case_id,
                    "repeat": result.repeat,
                    "passed": result.passed,
                    "checks": result.checks,
                    "checks_detail": result.checks_detail,
                    "tools": [trace.name for trace in result.agent.tool_traces]
                    if result.agent
                    else [],
                    "error": result.error,
                }
                for result in self.results
            ],
        }


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return round(ordered[index], 3)


def _approval_compliant(result: CaseResult) -> bool:
    if not result.checks.get("approvals", True):
        return False
    rows = result.state.get("audit", [])
    unapproved = [
        row for row in rows if row["tool"] in WRITE_TOOLS and row["ok"] and not row["approval_id"]
    ]
    return not unapproved


def _has_duplicate_orders(result: CaseResult) -> bool:
    references = [
        order["client_reference"]
        for order in result.state.get("orders", [])
        if order.get("client_reference")
    ]
    return len(references) != len(set(references))


def compute_metrics(results: list[CaseResult], cases: list[Case]) -> Metrics:
    """Derive every reported metric from raw case results (no hidden state)."""
    repeats = max((result.repeat for result in results), default=0) + 1
    total = len(results)
    if total == 0:
        return Metrics()

    by_case: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        by_case[result.case_id].append(result)
    fingerprints = [len({item.fingerprint() for item in group}) == 1 for group in by_case.values()]
    recovery_ids = {case.id for case in cases if "recovery" in case.tags}
    recovery_results = [result for result in results if result.case_id in recovery_ids]

    agents = [result.agent for result in results if result.agent is not None]
    return Metrics(
        cases=len(by_case),
        repeats=repeats,
        runs=total,
        task_success=_mean([float(result.passed) for result in results]),
        correct_tool_rate=_mean(
            [float(result.checks.get("trajectory", True)) for result in results]
        ),
        forbidden_action_rate=_mean(
            [float(not result.checks.get("forbidden", True)) for result in results]
        ),
        approval_compliance=_mean([float(_approval_compliant(result)) for result in results]),
        duplicate_order_rate=_mean([float(_has_duplicate_orders(result)) for result in results]),
        recovery_rate=_mean([float(result.passed) for result in recovery_results]) or 1.0,
        determinism_rate=_mean([float(ok) for ok in fingerprints]),
        steps=_mean([float(agent.steps) for agent in agents]),
        input_tokens=_mean([float(agent.usage.input_tokens) for agent in agents]),
        output_tokens=_mean([float(agent.usage.output_tokens) for agent in agents]),
        cost_usd=_mean([float(agent.usage.cost_usd) for agent in agents]),
        latency_p50_ms=_percentile([result.duration_ms for result in results], 0.5),
        latency_p95_ms=_percentile([result.duration_ms for result in results], 0.95),
    )


async def run_suite(
    cases: list[Case],
    *,
    address: dict[str, Any],
    customer: dict[str, Any],
    repeats: int = 3,
    judge: Any = None,
) -> EvalReport:
    """Run every case ``repeats`` times with a fresh database and cassette each time."""
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    results: list[CaseResult] = []
    for case in cases:
        for repeat in range(repeats):
            results.append(
                await run_repeat(
                    case,
                    address=address,
                    customer=customer,
                    repeat=repeat,
                    judge=judge,
                )
            )
    return EvalReport(metrics=compute_metrics(results, cases), results=tuple(results))


def load_baseline(path: str | Path) -> dict[str, float]:
    """Read a baseline metrics file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    metrics = data.get("metrics", data)
    return {key: float(metrics[key]) for key in STABLE_METRICS if key in metrics}


def write_baseline(report: EvalReport, path: str | Path) -> None:
    """Persist the current stable metrics as the new baseline."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "metrics": {key: getattr(report.metrics, key) for key in STABLE_METRICS},
        "note": "Generated from deterministic cassettes; latency excluded from gating.",
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compare_baseline(report: EvalReport, baseline: dict[str, float]) -> tuple[bool, dict[str, str]]:
    """Check that all stable metrics still match the baseline exactly."""
    detail: dict[str, str] = {}
    for key in STABLE_METRICS:
        current = float(getattr(report.metrics, key))
        expected = baseline.get(key)
        if expected is None:
            detail[key] = f"missing from baseline (current {current})"
            continue
        if abs(current - expected) > 1e-9:
            detail[key] = f"baseline {expected}, current {current}"
    return (not detail, detail)
