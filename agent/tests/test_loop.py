"""The hand-written loop: tool calls, traces, usage and step budgets."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from aintegrator_agent.llm import LLMReply, Message, ScriptedLLM, ToolCall, ToolDefinition
from aintegrator_agent.loop import AgentLoop
from aintegrator_agent.result import CostModel

_TOOL_CALL = ToolCall(id="call-1", name="check_eligibility", arguments={})


class EchoTools:
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return ()

    async def execute(self, call: ToolCall) -> str:
        return json.dumps({"ok": True, "echo": call.name})


async def test_loop_executes_tool_then_returns_final_answer() -> None:
    llm = ScriptedLLM(
        [
            LLMReply(text="Checking.", tool_calls=(_TOOL_CALL,), input_tokens=10, output_tokens=5),
            LLMReply(text="Done.", input_tokens=7, output_tokens=3),
        ]
    )
    loop = AgentLoop(llm, EchoTools(), max_steps=4, cost_model=CostModel(1.0, 2.0))
    result = await loop.run("check coverage")

    assert result.answer == "Done."
    assert result.steps == 2
    assert result.reached_step_limit is False
    assert [trace.name for trace in result.tool_traces] == ["check_eligibility"]
    assert result.tool_traces[0].error is False
    assert result.usage.input_tokens == 17
    assert result.usage.output_tokens == 8
    assert result.usage.cost_usd == pytest.approx((17 * 1.0 + 8 * 2.0) / 1_000_000)
    assert result.messages[-1].role == "assistant"
    assert result.messages[-2].role == "tool"
    assert result.messages[-2].tool_call_id == "call-1"
    assert len(llm.seen) == 2
    assert llm.seen[1][-1].role == "tool"


async def test_loop_stops_at_step_budget() -> None:
    llm = ScriptedLLM([LLMReply(text="still working", tool_calls=(_TOOL_CALL,)) for _ in range(5)])
    loop = AgentLoop(llm, EchoTools(), max_steps=2)
    result = await loop.run("loop forever")

    assert result.steps == 2
    assert result.reached_step_limit is True
    assert len(result.tool_traces) == 2
    assert len(llm.seen) == 2


async def test_failed_tool_is_recorded_as_error() -> None:
    class FailingTools(EchoTools):
        async def execute(self, call: ToolCall) -> str:
            return json.dumps({"ok": False, "error": {"code": "X", "message": "no"}})

    llm = ScriptedLLM(
        [
            LLMReply(tool_calls=(_TOOL_CALL,)),
            LLMReply(text="The tool failed."),
        ]
    )
    result = await AgentLoop(llm, FailingTools()).run("try")
    assert result.tool_traces[0].error is True
    assert result.answer == "The tool failed."


async def test_history_is_kept_for_follow_up_turns() -> None:
    llm = ScriptedLLM([LLMReply(text="first"), LLMReply(text="second")])
    loop = AgentLoop(llm, EchoTools())
    first = await loop.run("hello")
    second = await loop.run("and again", history=first.messages)

    assert [message.content for message in second.messages if message.role == "user"] == [
        "hello",
        "and again",
    ]
    assert isinstance(second.messages[0], Message)


class _RecordingSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}
        self.status: str | None = None
        self.exceptions: list[BaseException] = []

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def record_exception(self, exception: BaseException) -> None:
        self.exceptions.append(exception)

    def set_status(self, status: str, description: str | None = None) -> None:
        self.status = status


class _RecordingTracer:
    def __init__(self) -> None:
        self.names: list[str] = []
        self.spans: list[_RecordingSpan] = []

    @contextmanager
    def start_span(self, name: str, attributes: Any = None) -> Iterator[_RecordingSpan]:
        span = _RecordingSpan()
        span.attributes.update(attributes or {})
        self.names.append(name)
        self.spans.append(span)
        yield span


async def test_loop_emits_redacted_agent_spans() -> None:
    tracer = _RecordingTracer()
    llm = ScriptedLLM(
        [
            LLMReply(tool_calls=(_TOOL_CALL,), input_tokens=10, output_tokens=5),
            LLMReply(text="Done.", output_tokens=3),
        ]
    )
    result = await AgentLoop(llm, EchoTools(), tracer=tracer).run("check coverage")

    assert result.answer == "Done."
    assert tracer.names == ["agent.run", "agent.llm", "agent.tool", "agent.llm"]
    run_span = tracer.spans[0]
    assert run_span.attributes["steps"] == 2
    assert run_span.attributes["tool_calls"] == 1
    assert run_span.attributes["input_tokens"] == 10
    assert run_span.attributes["output_tokens"] == 8
    assert run_span.status == "ok"
    assert isinstance(run_span.attributes["run_id"], str)
    assert tracer.spans[2].attributes["tool"] == "check_eligibility"
    assert tracer.spans[2].attributes["run_id"] == run_span.attributes["run_id"]
    assert tracer.spans[2].status == "ok"
