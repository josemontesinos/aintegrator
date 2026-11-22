"""CLI wiring without a real LLM provider."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aintegrator_agent import cli
from aintegrator_agent.config import AgentSettings
from aintegrator_agent.llm import LLMReply, ScriptedLLM
from aintegrator_agent.loop import AgentLoop


def _drive(coroutine: object) -> object:
    with asyncio.Runner() as runner:
        return runner.run(coroutine)  # type: ignore[arg-type]


class _StubService:
    async def aclose(self) -> None:
        return None


def test_run_prints_answer_and_trace(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    llm = ScriptedLLM([LLMReply(text="One offer found.")])
    loop = AgentLoop(llm, EchoTools())
    monkeypatch.setattr(cli, "build_agent", lambda settings: (loop, _StubService(), llm))
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
    monkeypatch.setattr(cli, "build_agent", lambda settings: (loop, _StubService(), llm))
    monkeypatch.setattr(cli.asyncio, "run", _drive)

    assert cli.main(["run", "hello", "--json"]) == 0
    assert '"answer": "done"' in capsys.readouterr().out


class EchoTools:
    def definitions(self) -> tuple[Any, ...]:
        return ()

    async def execute(self, call: Any) -> str:
        return '{"ok": true}'


def test_settings_are_env_driven(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "fictional-test-model")
    monkeypatch.setenv("AGENT_MAX_STEPS", "3")
    settings = AgentSettings()
    assert settings.llm_model == "fictional-test-model"
    assert settings.agent_max_steps == 3
    assert settings.llm_api_key is None
