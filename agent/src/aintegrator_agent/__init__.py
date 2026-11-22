"""Hand-written tool-calling agent for AIntegrator.

Phase 2 runs tools in-process; phase 3 swaps in an MCP client behind the same
``ToolExecutor`` interface.
"""

from aintegrator_agent.config import AgentSettings, get_agent_settings
from aintegrator_agent.llm import AnthropicLLM, LLMClient, LLMReply, Message, ScriptedLLM
from aintegrator_agent.loop import AgentLoop
from aintegrator_agent.result import AgentResult, CostModel, ToolTrace, Usage
from aintegrator_agent.tools import InProcessTools, ToolExecutor

__all__ = [
    "AgentLoop",
    "AgentResult",
    "AgentSettings",
    "AnthropicLLM",
    "CostModel",
    "InProcessTools",
    "LLMClient",
    "LLMReply",
    "Message",
    "ScriptedLLM",
    "ToolExecutor",
    "ToolTrace",
    "Usage",
    "__version__",
    "get_agent_settings",
]

__version__ = "0.1.0"
