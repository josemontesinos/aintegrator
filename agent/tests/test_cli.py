"""CLI wiring without a real LLM provider or MCP subprocess."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from aintegrator_agent import cli
from aintegrator_agent.config import AgentSettings
from aintegrator_agent.llm import LLMReply, ScriptedLLM
from aintegrator_agent.loop import AgentLoop


def _drive(coroutine: object) -> object:
    with asyncio.Runner() as runner:
        return runner.run(coroutine)  # type: ignore[arg-type]


class EchoTools:
    def definitions(self) -> tuple[Any, ...]:
        return ()

    async def execute(self, call: Any) -> str:
        return '{"ok": true}'


def _fake_build_agent(loop: AgentLoop) -> Any:
    @asynccontextmanager
    async def fake(settings: AgentSettings) -> AsyncIterator[AgentLoop]:
        del settings
        yield loop

    return fake


def test_run_prints_answer_and_trace(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    llm = ScriptedLLM([LLMReply(text="One offer found.")])
    loop = AgentLoop(llm, EchoTools())
    monkeypatch.setattr(cli, "build_agent", _fake_build_agent(loop))
    monkeypatch.setattr(cli.asyncio, "run", _drive)

    assert cli.main(["run", "check coverage", "--verbose"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "One offer found."
    assert "usage:" in captured.err


def test_run_json_flag_emits_full_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    llm = ScriptedLLM([LLMReply(text="done")])
    loop = AgentLoop(llm, EchoTools())
    monkeypatch.setattr(cli, "build_agent", _fake_build_agent(loop))
    monkeypatch.setattr(cli.asyncio, "run", _drive)

    assert cli.main(["run", "hello", "--json"]) == 0
    assert '"answer": "done"' in capsys.readouterr().out


def test_settings_are_env_driven(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "fictional-test-model")
    monkeypatch.setenv("AGENT_MAX_STEPS", "3")
    settings = AgentSettings()
    assert settings.llm_model == "fictional-test-model"
    assert settings.agent_max_steps == 3
    assert settings.llm_api_key is None
