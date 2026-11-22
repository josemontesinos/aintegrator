"""Default wiring for the CLI: real LLM, in-process tools, real service."""

from __future__ import annotations

from aintegrator_integration.factory import build_service
from aintegrator_integration.service import ConnectivityService

from aintegrator_agent.config import AgentSettings
from aintegrator_agent.llm import AnthropicLLM
from aintegrator_agent.loop import AgentLoop
from aintegrator_agent.result import CostModel
from aintegrator_agent.tools import InProcessTools

__all__ = ["build_agent"]


def build_agent(
    settings: AgentSettings,
    service: ConnectivityService | None = None,
) -> tuple[AgentLoop, ConnectivityService, AnthropicLLM]:
    """Return the loop and the resources the caller must close."""
    runtime = service or build_service()
    llm = AnthropicLLM(
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
    )
    tools = InProcessTools(runtime)
    loop = AgentLoop(
        llm,
        tools,
        max_steps=settings.agent_max_steps,
        cost_model=CostModel(
            input_per_million=settings.cost_input_per_million,
            output_per_million=settings.cost_output_per_million,
        ),
    )
    return loop, runtime, llm
