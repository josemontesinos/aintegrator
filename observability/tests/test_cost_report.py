"""Tests for the deterministic cost report CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aintegrator_observability.cost_report import (
    build_report,
    main,
    render_markdown,
)

_SAMPLE: dict[str, Any] = {
    "config": {
        "model": "fictional-model",
        "cost_input_per_million": 1.0,
        "cost_output_per_million": 2.0,
    },
    "metrics": {"runs": 3, "cost_usd": 0.00047},
    "runs": [
        {
            "profile": "standard",
            "provider": "fibernet",
            "usage": {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.0002},
        },
        {
            "profile": "read-only",
            "provider": "none",
            "usage": {"input_tokens": 50, "output_tokens": 10, "cost_usd": 0.00007},
        },
        {
            "profile": "standard",
            "provider": "fibernet",
            "usage": {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.0002},
        },
    ],
}


def test_build_report_aggregates_profiles_providers_and_volume() -> None:
    report = build_report(_SAMPLE, daily_runs=10, days=30)

    assert report.model == "fictional-model"
    assert report.runs == 3
    assert abs(report.cost_per_run - 0.000157) < 1e-9
    assert abs(report.projected_monthly_cost - 0.0471) < 1e-9
    assert [bucket.key for bucket in report.profiles] == ["read-only", "standard"]
    assert [bucket.key for bucket in report.providers] == ["fibernet", "none"]
    assert report.profiles[1].runs == 2
    assert abs(report.profiles[1].cost_per_run - 0.0002) < 1e-9


def test_render_markdown_is_stable_and_labelled_as_an_estimate() -> None:
    report = build_report(_SAMPLE, daily_runs=10, days=30)
    rendered = render_markdown(report)

    assert rendered.startswith("# AIntegrator cost report")
    assert "fictional-model" in rendered
    assert "By tool profile" in rendered
    assert "By provider" in rendered
    assert "planning assumption" in rendered
    assert "Projected monthly cost" in rendered
    assert rendered == render_markdown(build_report(_SAMPLE, daily_runs=10, days=30))


def test_main_writes_and_checks_without_drift(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    output = tmp_path / "cost.md"
    report_path.write_text(json.dumps(_SAMPLE), encoding="utf-8")
    base_args = [
        "--report",
        str(report_path),
        "--output",
        str(output),
        "--daily-runs",
        "10",
        "--days",
        "30",
    ]

    assert main(base_args) == 0
    expected = output.read_text(encoding="utf-8")
    assert main([*base_args, "--check"]) == 0

    output.write_text(expected + "\n", encoding="utf-8")
    assert main([*base_args, "--check"]) == 1
    assert output.read_text(encoding="utf-8") == expected + "\n"


def test_build_report_handles_baseline_only_input() -> None:
    report = build_report({"metrics": {"runs": 2, "cost_usd": 0.01}}, daily_runs=0, days=0)
    assert report.runs == 2
    assert abs(report.cost_per_run - 0.005) < 1e-9
    assert report.profiles == ()
    assert report.projected_monthly_cost == 0.0
