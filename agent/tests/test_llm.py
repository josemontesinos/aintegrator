"""LLM boundary: message conversion and response normalization."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from aintegrator_agent.llm import (
    AnthropicLLM,
    LLMReply,
    Message,
    ScriptedLLM,
    ToolCall,
    ToolDefinition,
    to_provider_messages,
)


def test_message_conversion_covers_all_roles() -> None:
    call = ToolCall(id="t1", name="get_order", arguments={"order_id": "abc"})
    converted = to_provider_messages(
        [
            Message(role="user", content="hello"),
            Message(role="assistant", content="one moment", tool_calls=(call,)),
            Message(role="tool", content='{"ok":true}', tool_call_id="t1"),
        ]
    )
    assert converted[0] == {"role": "user", "content": "hello"}
    assert converted[1]["role"] == "assistant"
    assert converted[1]["content"][0] == {"type": "text", "text": "one moment"}
    assert converted[1]["content"][1] == {
        "type": "tool_use",
        "id": "t1",
        "name": "get_order",
        "input": {"order_id": "abc"},
    }
    assert converted[2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": '{"ok":true}'}],
    }


async def test_scripted_llm_records_messages_and_exhausts() -> None:
    reply = LLMReply(text="ok")
    llm = ScriptedLLM([reply])
    assert (
        await llm.complete(messages=[Message(role="user", content="hi")], tools=(), system="s")
        is reply
    )
    assert llm.seen[0][0].content == "hi"
    with pytest.raises(RuntimeError, match="no replies"):
        await llm.complete(messages=[], tools=(), system="s")


async def test_scripted_llm_supports_callable_replies() -> None:
    llm = ScriptedLLM([lambda messages: LLMReply(text=f"saw {len(messages)} messages")])
    reply = await llm.complete(messages=[Message(role="user", content="hi")], tools=(), system="s")
    assert reply.text == "saw 1 messages"


async def test_anthropic_client_normalizes_tool_use(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = AnthropicLLM(api_key="test-key", model="claude-sonnet-5-5", max_tokens=64)

    async def fake_create(**kwargs: Any) -> SimpleNamespace:
        assert kwargs["model"] == "claude-sonnet-5-5"
        assert kwargs["max_tokens"] == 64
        assert kwargs["tools"][0]["name"] == "get_order"
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="Let me check"),
                SimpleNamespace(
                    type="tool_use",
                    id="t1",
                    name="get_order",
                    input={"order_id": "abc"},
                ),
            ],
            stop_reason="tool_use",
            usage=SimpleNamespace(input_tokens=12, output_tokens=4),
        )

    monkeypatch.setattr(llm._client.messages, "create", fake_create)
    reply = await llm.complete(
        messages=[Message(role="user", content="status?")],
        tools=[ToolDefinition("get_order", "describe", {"type": "object"})],
        system="be brief",
    )
    assert reply.text == "Let me check"
    assert reply.stop_reason == "tool_use"
    assert reply.tool_calls == (ToolCall(id="t1", name="get_order", arguments={"order_id": "abc"}),)
    assert (reply.input_tokens, reply.output_tokens) == (12, 4)
    await llm.aclose()
