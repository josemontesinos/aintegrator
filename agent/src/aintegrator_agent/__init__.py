"""Hand-written tool-calling agent for AIntegrator.

Tools are reached over MCP behind the ``ToolExecutor`` interface; phase 4 adds
a CLI approval policy on top of the server-side hard gate.
"""

from aintegrator_agent.approval import ApprovalPolicyExecutor
from aintegrator_agent.config import AgentSettings, get_agent_settings
from aintegrator_agent.llm import AnthropicLLM, LLMClient, LLMReply, Message, ScriptedLLM
from aintegrator_agent.loop import AgentLoop
from aintegrator_agent.mcp_client import MCPSession, MCPToolExecutor
from aintegrator_agent.result import AgentResult, CostModel, ToolTrace, Usage
from aintegrator_agent.tools import InProcessTools, ToolExecutor

__all__ = [
    "AgentLoop",
    "AgentResult",
    "AgentSettings",
    "AnthropicLLM",
    "ApprovalPolicyExecutor",
    "CostModel",
    "InProcessTools",
    "LLMClient",
    "LLMReply",
    "MCPSession",
    "MCPToolExecutor",
    "Message",
    "ScriptedLLM",
    "ToolExecutor",
    "ToolTrace",
    "Usage",
    "__version__",
    "get_agent_settings",
]

__version__ = "0.1.0"
