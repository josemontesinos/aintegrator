"""Static checks for the local tracing stack and the deterministic cost gate."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_compose_defines_local_otlp_collector_and_jaeger() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert {"otel-collector", "jaeger"} <= set(services)
    assert compose["x-integration-env"]["OTEL_ENABLED"] == "${OTEL_ENABLED:-false}"
    assert (
        compose["x-integration-env"]["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://otel-collector:4317"
    )
    assert any(
        "collector-config.yaml" in volume for volume in services["otel-collector"]["volumes"]
    )
    assert services["jaeger"]["environment"]["COLLECTOR_OTLP_ENABLED"] == "true"


def test_env_example_documents_phase_7_without_real_secrets() -> None:
    env = (ROOT / ".env.example").read_text(encoding="utf-8")

    for key in (
        "OTEL_ENABLED=",
        "OTEL_SERVICE_NAME=",
        "OTEL_EXPORTER_OTLP_ENDPOINT=",
        "COST_REPORT_DAILY_RUNS=",
        "COST_REPORT_DAYS=",
        "TF_VAR_name_prefix=",
    ):
        assert key in env
    assert "000000000000" in env  # every example ARN is an all-zero placeholder


def test_container_image_installs_the_optional_otlp_extra() -> None:
    dockerfile = (ROOT / "Dockerfile.providers").read_text(encoding="utf-8")
    assert "uv sync --all-packages --frozen --extra otlp" in dockerfile


def test_cost_and_terraform_targets_exist() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("eval-report:", "cost-report:", "cost-report-check:", "terraform-validate:"):
        assert target in makefile
    check_line = next(line for line in makefile.splitlines() if line.startswith("check:"))
    assert "cost-report-check" in check_line
