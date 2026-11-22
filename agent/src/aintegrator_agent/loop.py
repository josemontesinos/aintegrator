"""Hand-written tool-calling agent loop.

One iteration = one LLM response. Tool calls are executed concurrently-safe in
sequence, appended to the conversation, and the model is called again until it
answers or the step budget is exhausted.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence

from aintegrator_integration.security import Redactor

from aintegrator_agent.llm import LLMClient, Message, ToolCall
from aintegrator_agent.prompts import SYSTEM_PROMPT
from aintegrator_agent.result import AgentResult, CostModel, ToolTrace, Usage
from aintegrator_agent.tools import ToolExecutor

__all__ = ["AgentLoop"]


def _failed(result: str) -> bool:
    try:
        payload = json.loads(result)
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("ok") is False


class AgentLoop:
    """Small, explicit loop: no framework, no hidden memory between runs."""

    def __init__(
        self,
        llm: LLMClient,
        tools: ToolExecutor,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        max_steps: int = 8,
        cost_model: CostModel | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._system_prompt = system_prompt
        self._max_steps = max_steps
        self._cost_model = cost_model or CostModel()
        self._redactor = redactor or Redactor()

    async def run(self, task: str, *, history: Sequence[Message] = ()) -> AgentResult:
        """Run one task, optionally continuing a conversation from ``history``."""
        messages: list[Message] = [*history, Message(role="user", content=task)]
        traces: list[ToolTrace] = []
        usage = Usage()
        step = 0
        answer = ""
        reached_limit = False

        while step < self._max_steps:
            step += 1
            reply = await self._llm.complete(
                messages=messages,
                tools=self._tools.definitions(),
                system=self._system_prompt,
            )
            usage = usage.add(self._cost_model.usage_for(reply))
            answer = reply.text
            messages.append(
                Message(role="assistant", content=reply.text, tool_calls=reply.tool_calls)
            )
            if not reply.tool_calls:
                break
            for call in reply.tool_calls:
                started = time.perf_counter()
                result_text = await self._execute(call)
                duration_ms = (time.perf_counter() - started) * 1000
                safe_arguments = self._redactor.value(dict(call.arguments))
                safe_result = self._redactor.text(result_text)
                traces.append(
                    ToolTrace(
                        name=call.name,
                        arguments=safe_arguments,
                        result=safe_result,
                        duration_ms=duration_ms,
                        error=_failed(result_text),
                    )
                )
                messages.append(Message(role="tool", content=safe_result, tool_call_id=call.id))
        else:
            reached_limit = True
            answer = answer or "I reached the step limit before completing the task."

        return AgentResult(
            answer=answer,
            messages=tuple(messages),
            tool_traces=tuple(traces),
            usage=usage,
            steps=step,
            reached_step_limit=reached_limit,
        )

    async def _execute(self, call: ToolCall) -> str:
        try:
            return await self._tools.execute(call)
        except Exception as exc:  # pragma: no cover - a tool must not kill the loop
            return json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "TOOL_EXECUTION_FAILED",
                        "message": self._redactor.text(f"{type(exc).__name__}: {exc}"),
                        "retryable": False,
                    },
                }
            )
