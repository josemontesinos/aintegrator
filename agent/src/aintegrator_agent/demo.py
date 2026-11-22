"""Deterministic end-to-end agent demo: no API key, real providers.

The scripted LLM asks for eligibility once, then formats the tool result into a
human-readable answer. This exercises the same loop, tools and service as the
real CLI without depending on a model.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

from aintegrator_integration.factory import build_service
from aintegrator_integration.service import ConnectivityService

from aintegrator_agent.config import get_agent_settings
from aintegrator_agent.llm import LLMReply, Message, ScriptedLLM, ToolCall
from aintegrator_agent.loop import AgentLoop
from aintegrator_agent.result import AgentResult
from aintegrator_agent.tools import InProcessTools

__all__ = ["main", "run_demo"]

TASK = "Check connectivity offers at Fictional Street 12, Madrid."


def _format_tool_result(messages: Sequence[Message]) -> LLMReply:
    last = messages[-1]
    payload = json.loads(last.content)
    lines = ["Offers found:"]
    for provider in payload.get("providers", []):
        if provider.get("status") != "OK":
            lines.append(
                f"- {provider['provider']}: {provider['status']} ({provider.get('error')})"
            )
            continue
        for offer in provider.get("offers", []):
            lines.append(
                f"- {provider['provider']}: {offer['plan_name']} "
                f"({offer['offer_id']}) {offer['monthly_price_eur']:.2f} EUR/month"
            )
    return LLMReply(text="\n".join(lines))


async def run_demo(service: ConnectivityService | None = None) -> AgentResult:
    """Run the scripted loop; close externally supplied services yourself."""
    runtime = service or build_service()
    llm = ScriptedLLM(
        [
            LLMReply(
                text="I will check all three providers.",
                tool_calls=(
                    ToolCall(
                        id="demo-1",
                        name="check_eligibility",
                        arguments={
                            "street": "Fictional Street",
                            "number": "12",
                            "postal_code": "28001",
                            "city": "Madrid",
                            "country": "ES",
                        },
                    ),
                ),
            ),
            _format_tool_result,
        ]
    )
    settings = get_agent_settings()
    loop = AgentLoop(llm, InProcessTools(runtime), max_steps=settings.agent_max_steps)
    try:
        return await loop.run(TASK)
    finally:
        if service is None:
            await runtime.aclose()


def main() -> int:
    result = asyncio.run(run_demo())
    print(result.answer)
    print(f"\nsteps={result.steps} tool_calls={len(result.tool_traces)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
