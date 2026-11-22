"""Deterministic cost report derived from an eval report (no API key required).

The report answers two questions without guessing: what did one deterministic eval
run cost, and how does that cost split by tool profile and provider? A monthly figure
is a plainly labelled planning assumption driven by ``COST_REPORT_DAILY_RUNS`` and
``COST_REPORT_DAYS``; it is never presented as observed spend.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "Bucket",
    "CostReport",
    "build_report",
    "load_report",
    "main",
    "render_markdown",
]

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_REPORT = _ROOT / "evals" / "reports" / "latest.json"
_DEFAULT_OUTPUT = _ROOT / "docs" / "cost-report.md"


@dataclass(frozen=True, slots=True)
class Bucket:
    """Cost and token totals for one grouping key."""

    key: str
    runs: int
    input_tokens: float
    output_tokens: float
    cost_usd: float

    @property
    def cost_per_run(self) -> float:
        return self.cost_usd / self.runs if self.runs else 0.0


@dataclass(frozen=True, slots=True)
class CostReport:
    """Everything rendered into the Markdown report."""

    model: str
    input_per_million: float
    output_per_million: float
    runs: int
    input_tokens_per_run: float
    output_tokens_per_run: float
    cost_per_run: float
    daily_runs: int
    days: int
    profiles: tuple[Bucket, ...]
    providers: tuple[Bucket, ...]

    @property
    def projected_monthly_cost(self) -> float:
        return self.cost_per_run * self.daily_runs * self.days

    @property
    def projected_monthly_runs(self) -> int:
        return self.daily_runs * self.days


def _round(value: float, places: int = 6) -> float:
    return round(float(value), places)


def load_report(path: str | Path) -> dict[str, Any]:
    """Read an eval report written by ``python -m aintegrator_evals --json ...``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("eval report must be a JSON object")
    return data


def _buckets(runs: list[dict[str, Any]], field: str, fallback: str) -> tuple[Bucket, ...]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        key = run.get(field) or fallback
        grouped[str(key)].append(run)
    result = []
    for key, items in sorted(grouped.items()):
        result.append(
            Bucket(
                key=key,
                runs=len(items),
                input_tokens=_round(sum(_tokens(item, "input_tokens") for item in items), 2),
                output_tokens=_round(sum(_tokens(item, "output_tokens") for item in items), 2),
                cost_usd=_round(sum(_tokens(item, "cost_usd") for item in items)),
            )
        )
    return tuple(result)


def _tokens(item: dict[str, Any], field: str) -> float:
    usage = item.get("usage")
    if not isinstance(usage, dict):
        return 0.0
    value = usage.get(field, 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_report(
    data: dict[str, Any],
    *,
    daily_runs: int | None = None,
    days: int | None = None,
) -> CostReport:
    """Aggregate a deterministic eval report into token and cost buckets."""
    config_raw = data.get("config")
    config: dict[str, Any] = config_raw if isinstance(config_raw, dict) else {}
    metrics_raw = data.get("metrics")
    metrics: dict[str, Any] = metrics_raw if isinstance(metrics_raw, dict) else {}
    runs_raw = data.get("runs")
    raw_runs: list[Any] = runs_raw if isinstance(runs_raw, list) else []
    runs = [run for run in raw_runs if isinstance(run, dict)]

    volume = (
        daily_runs if daily_runs is not None else int(os.getenv("COST_REPORT_DAILY_RUNS", "100"))
    )
    horizon = days if days is not None else int(os.getenv("COST_REPORT_DAYS", "30"))
    if volume < 0 or horizon < 0:
        raise ValueError("daily runs and days must be non-negative")

    model = str(config.get("model") or os.getenv("LLM_MODEL") or "claude-sonnet-5-5")
    input_rate = float(
        config.get("cost_input_per_million") or os.getenv("COST_INPUT_PER_MILLION") or 3.0
    )
    output_rate = float(
        config.get("cost_output_per_million") or os.getenv("COST_OUTPUT_PER_MILLION") or 15.0
    )

    if runs:
        total_cost = _round(sum(_tokens(run, "cost_usd") for run in runs))
        total_input = _round(sum(_tokens(run, "input_tokens") for run in runs), 2)
        total_output = _round(sum(_tokens(run, "output_tokens") for run in runs), 2)
        observed_runs = len(runs)
    else:
        # Baseline-only input: still useful, but every bucket stays empty.
        total_cost = _round(float(metrics.get("cost_usd", 0.0)))
        total_input = _round(float(metrics.get("input_tokens", 0.0)), 2)
        total_output = _round(float(metrics.get("output_tokens", 0.0)), 2)
        observed_runs = int(metrics.get("runs", 0) or 0)

    return CostReport(
        model=model,
        input_per_million=input_rate,
        output_per_million=output_rate,
        runs=observed_runs,
        input_tokens_per_run=_round(total_input / observed_runs, 2) if observed_runs else 0.0,
        output_tokens_per_run=_round(total_output / observed_runs, 2) if observed_runs else 0.0,
        cost_per_run=_round(total_cost / observed_runs) if observed_runs else 0.0,
        daily_runs=volume,
        days=horizon,
        profiles=_buckets(runs, "profile", "unspecified"),
        providers=_buckets(runs, "provider", "unspecified"),
    )


def _table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _bucket_rows(buckets: tuple[Bucket, ...]) -> list[tuple[str, ...]]:
    return [
        (
            bucket.key,
            str(bucket.runs),
            f"{bucket.input_tokens / bucket.runs:.2f}" if bucket.runs else "0.00",
            f"{bucket.output_tokens / bucket.runs:.2f}" if bucket.runs else "0.00",
            f"${bucket.cost_per_run:.6f}",
            f"${bucket.cost_per_run * 1000:.3f}",
        )
        for bucket in buckets
    ]


def _findings(report: CostReport) -> list[str]:
    if not report.profiles:
        return ["No per-run usage was found; generate an eval report with `--json` first."]
    ranking = sorted(report.profiles, key=lambda bucket: bucket.cost_per_run, reverse=True)
    cheapest = ranking[-1]
    priciest = ranking[0]
    findings = [
        f"Most expensive profile per run: `{priciest.key}` at ${priciest.cost_per_run:.6f}.",
        f"Cheapest profile per run: `{cheapest.key}` at ${cheapest.cost_per_run:.6f}.",
    ]
    standard = next((bucket for bucket in ranking if bucket.key == "standard"), None)
    read_only = next((bucket for bucket in ranking if bucket.key == "read-only"), None)
    if standard and read_only:
        delta = standard.cost_per_run - read_only.cost_per_run
        findings.append(
            f"Read-only runs save ${delta:.6f} per run versus `standard` on these cassettes."
        )
    findings.append(
        "Costs are estimates from fictional rates; latency and real provider spend are excluded."
    )
    return findings


def render_markdown(report: CostReport) -> str:
    """Render a stable, checked-in Markdown report (no timestamp or host paths)."""
    lines = [
        "# AIntegrator cost report",
        "",
        "All figures are derived from the deterministic offline eval report. They use fictional",
        "pricing and cassette token counts; the monthly number is a planning assumption, not",
        "observed spend.",
        "",
        "## Pricing and volume",
        "",
        f"- Model: `{report.model}`",
        f"- Input: ${report.input_per_million:.2f} / 1M tokens",
        f"- Output: ${report.output_per_million:.2f} / 1M tokens",
        f"- Observed eval runs: {report.runs}",
        f"- Planning volume: {report.daily_runs} runs/day x {report.days} days "
        f"= {report.projected_monthly_runs} runs/month",
        "",
        "## Per-run cost",
        "",
        _table(
            ("Metric", "Value"),
            [
                ("Input tokens / run", f"{report.input_tokens_per_run:.2f}"),
                ("Output tokens / run", f"{report.output_tokens_per_run:.2f}"),
                ("Estimated cost / run", f"${report.cost_per_run:.6f}"),
                ("Projected monthly cost", f"${report.projected_monthly_cost:.3f}"),
            ],
        ),
        "",
        "## By tool profile",
        "",
        _table(
            ("Profile", "Runs", "In/run", "Out/run", "Cost/run", "Cost / 1k runs"),
            _bucket_rows(report.profiles),
        ),
        "",
        "## By provider",
        "",
        _table(
            ("Provider", "Runs", "In/run", "Out/run", "Cost/run", "Cost / 1k runs"),
            _bucket_rows(report.providers),
        ),
        "",
        "## Findings",
        "",
    ]
    lines.extend(f"- {finding}" for finding in _findings(report))
    lines.extend(
        [
            "",
            "Regenerate with `make cost-report`; CI verifies the checked-in copy with",
            "`make cost-report-check`. No API key or running services are needed.",
            "",
        ]
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aintegrator-cost-report", description="Render the deterministic cost report."
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=_DEFAULT_REPORT,
        help="eval report JSON (default: evals/reports/latest.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="Markdown destination (default: docs/cost-report.md)",
    )
    parser.add_argument("--daily-runs", type=int, default=None, help="planning runs per day")
    parser.add_argument("--days", type=int, default=None, help="planning horizon in days")
    parser.add_argument(
        "--check", action="store_true", help="verify the output file without writing it"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns 0 on success and 1 when ``--check`` drifts."""
    args = _parser().parse_args(argv)
    report = build_report(load_report(args.report), daily_runs=args.daily_runs, days=args.days)
    rendered = render_markdown(report)
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            print(f"Cost report drift: {args.output}", file=sys.stderr)
            return 1
        print(f"Cost report up to date: {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"Wrote cost report to {args.output}")
    print(f"  ${report.cost_per_run:.6f}/run, ${report.projected_monthly_cost:.3f}/month")
    return 0


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
