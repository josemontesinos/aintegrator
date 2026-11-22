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
from aintegrator_observability import (
    Tracer,
    current_run_id,
    get_tracer,
    new_run_id,
    reset_run_id,
    set_run_id,
)

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


def _attributes(values: dict[str, object]) -> dict[str, object]:
    run_id = current_run_id()
    if run_id:
        values["run_id"] = run_id
    return values


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
        tracer: Tracer | None = None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._system_prompt = system_prompt
        self._max_steps = max_steps
        self._cost_model = cost_model or CostModel()
        self._redactor = redactor or Redactor()
        self.tracer: Tracer = tracer or get_tracer()

    async def run(self, task: str, *, history: Sequence[Message] = ()) -> AgentResult:
        """Run one task, optionally continuing a conversation from ``history``."""
        messages: list[Message] = [*history, Message(role="user", content=task)]
        traces: list[ToolTrace] = []
        usage = Usage()
        step = 0
        answer = ""
        reached_limit = False
        run_id = new_run_id()
        token = set_run_id(run_id)
        try:
            with self.tracer.start_span(
                "agent.run",
                _attributes(
                    {
                        "run_id": run_id,
                        "max_steps": self._max_steps,
                        "history_messages": len(history),
                    }
                ),
            ) as span:
                try:
                    while step < self._max_steps:
                        step += 1
                        definitions = self._tools.definitions()
                        with self.tracer.start_span(
                            "agent.llm",
                            _attributes(
                                {
                                    "model": getattr(self._llm, "model", "scripted"),
                                    "messages": len(messages),
                                    "tools": len(definitions),
                                }
                            ),
                        ) as llm_span:
                            reply = await self._llm.complete(
                                messages=messages,
                                tools=definitions,
                                system=self._system_prompt,
                            )
                            llm_span.set_attribute("input_tokens", reply.input_tokens)
                            llm_span.set_attribute("output_tokens", reply.output_tokens)
                            llm_span.set_attribute("tool_calls", len(reply.tool_calls))
                            llm_span.set_attribute("stop_reason", reply.stop_reason)
                        usage = usage.add(self._cost_model.usage_for(reply))
                        answer = reply.text
                        messages.append(
                            Message(
                                role="assistant",
                                content=reply.text,
                                tool_calls=reply.tool_calls,
                            )
                        )
                        if not reply.tool_calls:
                            break
                        for call in reply.tool_calls:
                            started = time.perf_counter()
                            with self.tracer.start_span(
                                "agent.tool", _attributes({"tool": call.name})
                            ) as tool_span:
                                result_text = await self._execute(call)
                                duration_ms = (time.perf_counter() - started) * 1000
                                failed = _failed(result_text)
                                tool_span.set_attribute("duration_ms", round(duration_ms, 3))
                                tool_span.set_attribute("error", failed)
                                tool_span.set_status("error" if failed else "ok")
                            safe_arguments = self._redactor.value(dict(call.arguments))
                            safe_result = self._redactor.text(result_text)
                            traces.append(
                                ToolTrace(
                                    name=call.name,
                                    arguments=safe_arguments,
                                    result=safe_result,
                                    duration_ms=duration_ms,
                                    error=failed,
                                )
                            )
                            messages.append(
                                Message(role="tool", content=safe_result, tool_call_id=call.id)
                            )
                    else:
                        reached_limit = True
                        answer = answer or "I reached the step limit before completing the task."

                    result = AgentResult(
                        answer=answer,
                        messages=tuple(messages),
                        tool_traces=tuple(traces),
                        usage=usage,
                        steps=step,
                        reached_step_limit=reached_limit,
                    )
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status("error", type(exc).__name__)
                    raise
                span.set_attribute("steps", step)
                span.set_attribute("tool_calls", len(traces))
                span.set_attribute("input_tokens", usage.input_tokens)
                span.set_attribute("output_tokens", usage.output_tokens)
                span.set_attribute("cost_usd", usage.cost_usd)
                span.set_attribute("reached_step_limit", reached_limit)
                span.set_status("ok")
                return result
        finally:
            reset_run_id(token)

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
