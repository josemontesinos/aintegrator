"""Value objects describing what the agent did and what it cost."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from aintegrator_agent.llm import LLMReply, Message

__all__ = ["AgentResult", "CostModel", "ToolTrace", "Usage"]


@dataclass(frozen=True, slots=True)
class ToolTrace:
    """One executed tool call, for debugging and eval scoring."""

    name: str
    arguments: dict[str, Any]
    result: str
    duration_ms: float
    error: bool = False


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
        )


@dataclass(frozen=True, slots=True)
class CostModel:
    """Linear token pricing; rates are configurable estimates."""

    input_per_million: float = 3.0
    output_per_million: float = 15.0

    def usage_for(self, reply: LLMReply) -> Usage:
        cost = (
            reply.input_tokens * self.input_per_million
            + reply.output_tokens * self.output_per_million
        ) / 1_000_000
        return Usage(
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            cost_usd=round(cost, 6),
        )


@dataclass(frozen=True, slots=True)
class AgentResult:
    """Final answer plus the complete execution trace."""

    answer: str
    messages: tuple[Message, ...]
    tool_traces: tuple[ToolTrace, ...] = ()
    usage: Usage = field(default_factory=Usage)
    steps: int = 0
    reached_step_limit: bool = False

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready representation for ``--json`` and the eval harness."""
        return asdict(self)
