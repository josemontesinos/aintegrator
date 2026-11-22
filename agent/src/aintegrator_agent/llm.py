"""LLM client abstraction with a real Anthropic client and a scripted test double.

The loop only depends on the ``LLMClient`` protocol, so eval cassettes and unit
tests never need an API key or a network connection.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from anthropic import AsyncAnthropic

__all__ = [
    "AnthropicLLM",
    "LLMClient",
    "LLMReply",
    "Message",
    "ScriptedLLM",
    "ScriptedReply",
    "ToolCall",
    "ToolDefinition",
    "to_provider_messages",
]

Role = Literal["user", "assistant", "tool"]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Message:
    """Transport-neutral conversation message.

    ``tool`` messages carry the result of a call in ``content`` and the matching
    ``tool_call_id``. ``assistant`` messages can carry one or more tool calls.
    """

    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """JSON-schema description exposed to the model."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LLMReply:
    """Normalized model response: text, tool calls and token usage."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    stop_reason: str = "end_turn"
    input_tokens: int = 0
    output_tokens: int = 0


ScriptedReply = LLMReply | Callable[[Sequence[Message]], LLMReply]


class LLMClient(Protocol):
    """Minimal interface the agent loop depends on."""

    async def complete(
        self,
        *,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        system: str,
    ) -> LLMReply: ...


def to_provider_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Convert our messages to Anthropic's message-parameter shape."""
    converted: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id or "",
                            "content": message.content,
                        }
                    ],
                }
            )
        elif message.role == "assistant":
            content: list[dict[str, Any]] = []
            if message.content:
                content.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            converted.append({"role": "assistant", "content": content})
        else:
            converted.append({"role": "user", "content": message.content})
    return converted


class AnthropicLLM:
    """Real LLM client. Construct with explicit settings; no module-level secret."""

    def __init__(self, *, api_key: str | None, model: str, max_tokens: int = 2048) -> None:
        options: dict[str, Any] = {}
        if api_key:
            options["api_key"] = api_key
        self._client = AsyncAnthropic(**options)
        self.model = model
        self.max_tokens = max_tokens

    async def aclose(self) -> None:
        await self._client.close()

    async def complete(
        self,
        *,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        system: str,
    ) -> LLMReply:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": to_provider_messages(messages),
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in tools
            ],
        }
        response = await self._client.messages.create(**params)
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                arguments = cast("dict[str, Any]", block.input)
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(arguments)))
        return LLMReply(
            text="\n".join(text_parts),
            tool_calls=tuple(calls),
            stop_reason=str(response.stop_reason),
            input_tokens=int(response.usage.input_tokens),
            output_tokens=int(response.usage.output_tokens),
        )


class ScriptedLLM:
    """Deterministic LLM for tests: returns queued replies in order."""

    def __init__(self, replies: Sequence[ScriptedReply]) -> None:
        self._replies = list(replies)
        self.seen: list[tuple[Message, ...]] = []

    async def aclose(self) -> None:
        return None

    async def complete(
        self,
        *,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        system: str,
    ) -> LLMReply:
        del tools, system
        self.seen.append(tuple(messages))
        if not self._replies:
            raise RuntimeError("ScriptedLLM has no replies left")
        reply = self._replies.pop(0)
        return reply(messages) if callable(reply) else reply
