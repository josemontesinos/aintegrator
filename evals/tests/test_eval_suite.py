"""Unit tests for the deterministic eval harness, graders and baseline gate."""

from __future__ import annotations

from pathlib import Path

from aintegrator_agent.result import AgentResult, ToolTrace
from aintegrator_evals.cassette import CassetteLLM, load_cases, load_document, resolve_binding
from aintegrator_evals.graders import PinnedLLMJudge, grade_case
from aintegrator_evals.models import Case, ExpectSpec, ReplySpec, ToolCallSpec
from aintegrator_evals.runner import (
    STABLE_METRICS,
    compare_baseline,
    compute_metrics,
    load_baseline,
    run_suite,
)

_ROOT = Path(__file__).resolve().parents[2]
_CASES = _ROOT / "evals" / "cases.yaml"
_BASELINE = _ROOT / "evals" / "baseline.json"
_ADDRESS = {
    "street": "Fictional Street",
    "number": "12",
    "postal_code": "28001",
    "city": "Madrid",
    "country": "ES",
}
_CUSTOMER = {"name": "Ada Example", "email": "ada@example.invalid"}


def test_suite_has_at_least_30_unique_cases() -> None:
    cases = load_cases(_CASES)
    assert len(cases) >= 30
    assert len({case.id for case in cases}) == len(cases)
    assert all(case.cassette for case in cases)


def test_document_exposes_shared_fragments() -> None:
    document = load_document(_CASES)
    assert document.version == 1
    assert document.address["city"] == "Madrid"
    assert document.customer["name"] == "Ada Example"


async def test_cassette_binds_shared_fragments_and_tool_results() -> None:
    llm = CassetteLLM(
        [
            ReplySpec(
                tool_calls=[ToolCallSpec(name="check_eligibility", arguments="{{ address }}")]
            ),
            ReplySpec(text="best offer: {{ providers[0].offers[0].offer_id }}"),
        ],
        context={"address": _ADDRESS},
    )
    first = await llm.complete(messages=[], tools=[], system="")
    assert first.tool_calls[0].arguments == _ADDRESS
    tool_message = '{"providers":[{"offers":[{"offer_id":"fibernet:A-1"}]}]}'
    from aintegrator_agent.llm import Message

    second = await llm.complete(
        messages=[Message(role="tool", content=tool_message, tool_call_id="c1")],
        tools=[],
        system="",
    )
    assert second.text == "best offer: fibernet:A-1"


def test_resolve_binding_handles_nested_and_plain_values() -> None:
    value = {
        "plain": "Madrid",
        "city": "{{ customer.city }}",
        "id": "{{ providers[0].offers[1].offer_id }}",
    }
    resolved = resolve_binding(
        value,
        {"providers": [{"offers": [{"offer_id": "a"}, {"offer_id": "b"}]}]},
        {"customer": {"city": "Fictional City"}},
    )
    assert resolved == {"plain": "Madrid", "city": "Fictional City", "id": "b"}


async def test_deterministic_repeats_and_zero_forbidden_actions() -> None:
    cases = load_cases(_CASES)[:6]
    report = await run_suite(cases, address=_ADDRESS, customer=_CUSTOMER, repeats=3)
    assert report.metrics.cases == 6
    assert report.metrics.runs == 18
    assert report.metrics.task_success == 1.0
    assert report.metrics.determinism_rate == 1.0
    assert report.metrics.forbidden_action_rate == 0.0
    assert report.metrics.duplicate_order_rate == 0.0
    assert report.metrics.approval_compliance == 1.0
    assert report.metrics.input_tokens > 0


async def test_full_suite_passes_and_matches_baseline() -> None:
    cases = load_cases(_CASES)
    report = await run_suite(cases, address=_ADDRESS, customer=_CUSTOMER, repeats=1)
    assert report.metrics.runs == len(cases)
    assert all(result.passed for result in report.results)
    assert report.metrics.task_success == 1.0
    assert report.metrics.forbidden_action_rate == 0.0
    assert report.metrics.duplicate_order_rate == 0.0
    ok, detail = compare_baseline(report, load_baseline(_BASELINE))
    assert ok, detail
    assert set(load_baseline(_BASELINE)) == set(STABLE_METRICS)


async def test_baseline_detects_metric_drift() -> None:
    cases = load_cases(_CASES)[:1]
    report = await run_suite(cases, address=_ADDRESS, customer=_CUSTOMER, repeats=1)
    ok, detail = compare_baseline(report, {key: 0.0 for key in STABLE_METRICS})
    assert not ok
    assert "task_success" in detail


async def test_grader_flags_forbidden_tool_and_missing_answer() -> None:
    case = Case(
        id="grader-check",
        task="Do nothing",
        cassette=[ReplySpec(text="done")],
        expect=ExpectSpec(
            forbidden_tools=["create_order"],
            answer_contains=["approval"],
        ),
    )
    result = AgentResult(
        answer="done",
        messages=(),
        tool_traces=(
            ToolTrace(name="create_order", arguments={}, result='{"ok": true}', duration_ms=1),
        ),
    )
    checks, _detail = await grade_case(case, result, {})
    assert checks["forbidden"] is False
    assert checks["answer_contains"] is False


async def test_pinned_judge_skips_without_key() -> None:
    case = Case(id="judge", task="x", cassette=[ReplySpec(text="x")], expect=ExpectSpec(rubric="y"))
    result = AgentResult(answer="x", messages=())
    judge = PinnedLLMJudge(api_key=None)
    ok, reason = await judge.evaluate(case, result)
    assert ok and "skipped" in reason


def test_compute_metrics_handles_empty_suite() -> None:
    metrics = compute_metrics([], [])
    assert metrics.runs == 0
    assert metrics.task_success == 0.0
