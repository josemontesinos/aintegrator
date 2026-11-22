"""Default wiring for the CLI: real LLM, MCP tools and hard approval control."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from aintegrator_integration.factory import build_service
from aintegrator_integration.security import ApprovalService, Redactor
from aintegrator_integration.service import ConnectivityService

from aintegrator_agent.approval import ApprovalPolicy, ApprovalPolicyExecutor
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
    approve: ApprovalPolicy = "ask",
) -> AsyncIterator[AgentLoop]:
    """Yield a loop wired to MCP tools and close all owned resources afterwards.

    ``executor`` and ``llm_client`` are injection points for tests and demos.
    Production uses an Anthropic client and a stdio MCP server. The approval
    wrapper is always applied: even an injected executor must obey ``--approve``.
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
    redactor = Redactor(tuple(key for key in (settings.llm_api_key,) if key))
    approvals = ApprovalService(runtime.session_maker, runtime.settings, redactor=redactor)
    try:
        # ``connect_stdio`` must stay alive for the whole loop, so branch rather
        # than trying to reuse an already-closed context manager.
        if executor is not None:
            policy_tools = ApprovalPolicyExecutor(executor, approvals, policy=approve)
            yield AgentLoop(
                llm,
                policy_tools,
                max_steps=settings.agent_max_steps,
                cost_model=cost_model,
                redactor=redactor,
            )
        else:
            async with MCPToolExecutor.connect_stdio() as mcp_tools:
                policy_tools = ApprovalPolicyExecutor(mcp_tools, approvals, policy=approve)
                yield AgentLoop(
                    llm,
                    policy_tools,
                    max_steps=settings.agent_max_steps,
                    cost_model=cost_model,
                    redactor=redactor,
                )
    finally:
        await llm.aclose()
        if owns_service:
            await runtime.aclose()
