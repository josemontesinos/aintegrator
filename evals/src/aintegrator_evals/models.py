"""Typed schemas for eval cases, cassettes, results and aggregate metrics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aintegrator_agent.result import AgentResult
from pydantic import BaseModel, Field

__all__ = [
    "Case",
    "CaseResult",
    "CasesFile",
    "ExpectSpec",
    "Metrics",
    "ReplySpec",
    "SetupSpec",
    "ToolCallSpec",
]


class ToolCallSpec(BaseModel):
    """One scripted tool call. ``arguments_from`` copies a prior tool result."""

    name: str = Field(min_length=1, max_length=60)
    # A string is a ``{{ address }}``-style binding resolving to an argument dict.
    arguments: dict[str, Any] | str = Field(default_factory=dict)
    arguments_from: str | None = None
    id: str | None = None


class ReplySpec(BaseModel):
    """One scripted LLM reply, optionally with tool calls."""

    text: str = ""
    tool_calls: list[ToolCallSpec] = Field(default_factory=list)
    # Fictional per-reply defaults keep token/cost metrics meaningful without a key.
    input_tokens: int = 140
    output_tokens: int = 45
    stop_reason: str = "end_turn"


class SetupSpec(BaseModel):
    """Per-case environment: provider scenario, tool profile and settings."""

    scenario: str = "default"
    profile: str = "standard"
    settings: dict[str, Any] = Field(default_factory=dict)
    max_steps: int = 8


class ExpectSpec(BaseModel):
    """Deterministic expectations checked by the graders."""

    tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    answer_contains: list[str] = Field(default_factory=list)
    answer_not_contains: list[str] = Field(default_factory=list)
    rubric: str = ""
    rubric_keywords: list[str] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    approvals_consumed: int | None = None


class Case(BaseModel):
    """A single eval case with its checked-in cassette."""

    id: str = Field(min_length=1, max_length=80)
    task: str = Field(min_length=1, max_length=1000)
    tags: list[str] = Field(default_factory=list)
    approve: str = "ask"
    setup: SetupSpec = Field(default_factory=SetupSpec)
    cassette: list[ReplySpec] = Field(min_length=1)
    expect: ExpectSpec = Field(default_factory=ExpectSpec)


class CasesFile(BaseModel):
    """Top-level YAML document: reusable fragments plus all cases."""

    version: int = 1
    address: dict[str, Any] = Field(default_factory=dict)
    customer: dict[str, Any] = Field(default_factory=dict)
    cases: list[Case] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Everything a single repeat produces, for grading and reporting."""

    case_id: str
    repeat: int
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    checks_detail: dict[str, str] = field(default_factory=dict)
    agent: AgentResult | None = None
    state: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    error: str = ""

    def fingerprint(self) -> tuple[Any, ...]:
        """Stable identity used to verify N repeats are deterministic.

        Order/approval IDs are randomly generated per run, so they are normalised
        away. Everything else (trajectory, statuses, counts, error codes) must
        match exactly across repeats.
        """
        tools = tuple(t.name for t in self.agent.tool_traces) if self.agent else ()
        return (self.passed, tools, self._stable(self.normalised_state()))

    def normalised_state(self) -> dict[str, Any]:
        """State with per-run UUIDs removed."""
        orders = [
            {
                key: value
                for key, value in order.items()
                if key not in {"order_id", "idempotency_key", "client_reference"}
            }
            for order in self.state.get("orders", [])
        ]
        audit = [
            {key: value for key, value in row.items() if key != "approval_id"}
            for row in self.state.get("audit", [])
        ]
        return {"orders": orders, "audit": audit, "approvals": self.state.get("approvals", [])}

    @staticmethod
    def _stable(value: Any) -> str:
        import json

        return json.dumps(value, sort_keys=True, default=str)


@dataclass(frozen=True, slots=True)
class Metrics:
    """Aggregate eval metrics written to the report and compared to baseline."""

    cases: int = 0
    repeats: int = 0
    runs: int = 0
    task_success: float = 0.0
    correct_tool_rate: float = 0.0
    forbidden_action_rate: float = 0.0
    approval_compliance: float = 0.0
    duplicate_order_rate: float = 0.0
    recovery_rate: float = 0.0
    determinism_rate: float = 0.0
    steps: float = 0.0
    input_tokens: float = 0.0
    output_tokens: float = 0.0
    cost_usd: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)
