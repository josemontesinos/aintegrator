"""Default wiring for the CLI: real LLM, MCP tools, real service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from aintegrator_integration.factory import build_service
from aintegrator_integration.service import ConnectivityService

from aintegrator_agent.config import AgentSettings
from aintegrator_agent.llm import AnthropicLLM, LLMClient
from aintegrator_agent.loop import AgentLoop
from aintegrator_agent.mcp_client import MCPToolExecutor
from aintegrator_agent.result import CostModel
from aintegrator_agent.tools import ToolExecutor

__all__ = ["build_agent"]


class ClosableLLMClient(LLMClient, Protocol):
    """An LLM client the runtime can shut down."""

    async def aclose(self) -> None: ...


@asynccontextmanager
async def build_agent(
    settings: AgentSettings,
    *,
    service: ConnectivityService | None = None,
    executor: ToolExecutor | None = None,
    llm_client: ClosableLLMClient | None = None,
) -> AsyncIterator[AgentLoop]:
    """Yield a loop wired to MCP tools and close all owned resources afterwards.

    ``executor`` and ``llm_client`` are injection points for tests and demos.
    Production uses an Anthropic client and a stdio MCP server.
    """
    runtime = service or build_service()
    owns_service = service is None
    llm: ClosableLLMClient = llm_client or AnthropicLLM(
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
    )
    cost_model = CostModel(
        input_per_million=settings.cost_input_per_million,
        output_per_million=settings.cost_output_per_million,
    )
    try:
        if executor is not None:
            yield AgentLoop(
                llm, executor, max_steps=settings.agent_max_steps, cost_model=cost_model
            )
        else:
            async with MCPToolExecutor.connect_stdio() as mcp_tools:
                yield AgentLoop(
                    llm, mcp_tools, max_steps=settings.agent_max_steps, cost_model=cost_model
                )
    finally:
        await llm.aclose()
        if owns_service:
            await runtime.aclose()
