"""Load YAML cases and replay checked-in cassettes as a deterministic LLM."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from aintegrator_agent.llm import LLMReply, Message, ToolCall, ToolDefinition

from aintegrator_evals.models import Case, CasesFile, ReplySpec, ToolCallSpec

__all__ = ["CassetteLLM", "load_cases", "load_document", "resolve_binding"]

_BINDING = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def load_document(path: str | Path) -> CasesFile:
    """Read and validate a cases YAML file, including shared fragments."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    document = CasesFile.model_validate(raw)
    ids = [case.id for case in document.cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case IDs in eval suite")
    if not ids:
        raise ValueError("eval suite has no cases")
    return document


def load_cases(path: str | Path) -> list[Case]:
    """Read and validate only the cases from a YAML file."""
    return load_document(path).cases


def _lookup(root: Any, expression: str) -> Any:
    """Resolve ``last.providers[0].offers[0].offer_id`` style paths."""
    current = root
    for part in expression.split("."):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"([^\[]+)((?:\[\d+\])*)", part)
        if match is None:
            raise ValueError(f"invalid binding segment: {part!r}")
        key, indexes = match.groups()
        current = current[key] if isinstance(current, Mapping) else getattr(current, key)
        for index in re.findall(r"\[(\d+)\]", indexes):
            current = current[int(index)]
    return current


def _lookup_expression(expression: str, last: Any, context: Mapping[str, Any]) -> Any:
    """Resolve shared fragments first, then values from the last tool result."""
    for prefix in ("address", "customer"):
        if expression == prefix or expression.startswith(f"{prefix}."):
            rest = expression[len(prefix) :].lstrip(".")
            base = context.get(prefix, {})
            return _lookup(base, rest) if rest else base
    return _lookup(last, expression)


def resolve_binding(value: Any, last: Any, context: Mapping[str, Any] | None = None) -> Any:
    """Replace ``{{ path }}`` templates with shared or last-result values."""
    shared = context or {}
    if isinstance(value, str):
        match = _BINDING.fullmatch(value)
        if match:
            return _lookup_expression(match.group(1), last, shared)
        return _BINDING.sub(
            lambda found: str(_lookup_expression(found.group(1), last, shared)), value
        )
    if isinstance(value, Mapping):
        return {str(key): resolve_binding(item, last, shared) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [resolve_binding(item, last, shared) for item in value]
    return value


def _last_tool_payload(messages: Sequence[Message]) -> dict[str, Any]:
    for message in reversed(messages):
        if message.role == "tool":
            try:
                payload = json.loads(message.content)
            except ValueError:
                return {}
            return payload if isinstance(payload, dict) else {}
    return {}


class CassetteLLM:
    """Replay a fixed reply sequence; subsequent calls repeat the final reply."""

    def __init__(
        self,
        replies: Sequence[ReplySpec],
        *,
        case_id: str = "case",
        context: Mapping[str, Any] | None = None,
    ) -> None:
        if not replies:
            raise ValueError("a cassette needs at least one reply")
        self._replies = tuple(replies)
        self._case_id = case_id
        self._context: Mapping[str, Any] = context or {}
        self._index = 0
        self.replayed_final = False

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
        if self._index >= len(self._replies):
            self.replayed_final = True
            spec = self._replies[-1]
        else:
            spec = self._replies[self._index]
            self._index += 1
        last = _last_tool_payload(messages)
        calls = tuple(self._tool_calls(spec, last))
        return LLMReply(
            text=str(resolve_binding(spec.text, last, self._context)),
            tool_calls=calls,
            input_tokens=spec.input_tokens,
            output_tokens=spec.output_tokens,
            stop_reason=spec.stop_reason,
        )

    def _tool_calls(self, spec: ReplySpec, last: Any) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for position, item in enumerate(spec.tool_calls):
            calls.append(self._tool_call(item, last, position))
        return calls

    def _tool_call(self, item: ToolCallSpec, last: Any, position: int) -> ToolCall:
        arguments: dict[str, Any] = {}
        if item.arguments_from:
            copied = resolve_binding(
                _lookup_expression(item.arguments_from, last, self._context),
                last,
                self._context,
            )
            if isinstance(copied, Mapping):
                arguments.update(copied)
        resolved = resolve_binding(item.arguments, last, self._context)
        if isinstance(resolved, Mapping):
            arguments.update(resolved)
        call_id = item.id or f"{self._case_id}-r{self._index}-c{position}"
        return ToolCall(id=call_id, name=item.name, arguments=arguments)
