"""CLI for the eval suite: ``python -m aintegrator_evals`` (no API key needed)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from aintegrator_evals.cassette import load_document
from aintegrator_evals.graders import DeterministicJudge, PinnedLLMJudge
from aintegrator_evals.runner import (
    EvalReport,
    compare_baseline,
    load_baseline,
    run_suite,
    write_baseline,
)

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CASES = _ROOT / "evals" / "cases.yaml"
_DEFAULT_BASELINE = _ROOT / "evals" / "baseline.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aintegrator-evals", description=__doc__)
    parser.add_argument("--cases", type=Path, default=_DEFAULT_CASES)
    parser.add_argument("--baseline", type=Path, default=_DEFAULT_BASELINE)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--judge", choices=("offline", "llm"), default="offline")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--json", type=Path, default=None, help="write full results here")
    return parser


def _print_summary(report: EvalReport) -> None:
    print("Eval metrics")
    for key, value in report.metrics.as_dict().items():
        print(f"  {key:24} {value}")
    failures = [result for result in report.results if not result.passed]
    if failures:
        print(f"\n{len(failures)} failing run(s):")
        for result in failures[:20]:
            detail = " ".join(
                f"{name}={value}"
                for name, value in result.checks_detail.items()
                if not result.checks.get(name, True)
            )
            print(f"  {result.case_id} (repeat {result.repeat}): {detail or result.error}")


async def _main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    document = load_document(args.cases)
    judge = (
        PinnedLLMJudge(api_key=os.environ.get("EVAL_LLM_API_KEY"))
        if args.judge == "llm"
        else DeterministicJudge()
    )
    try:
        report = await run_suite(
            document.cases,
            address=document.address,
            customer=document.customer,
            repeats=args.repeats,
            judge=judge,
        )
    finally:
        if isinstance(judge, PinnedLLMJudge):
            await judge.aclose()

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report.as_dict(), indent=2) + "\n", encoding="utf-8")

    if args.update_baseline:
        write_baseline(report, args.baseline)
        print(f"Wrote baseline to {args.baseline}")
        _print_summary(report)
        return 0

    ok, detail = compare_baseline(report, load_baseline(args.baseline))
    report = replace(report, baseline_ok=ok, baseline_detail=detail)
    _print_summary(report)
    if detail:
        print("\nBaseline mismatches:")
        for key, message in detail.items():
            print(f"  {key}: {message}")
    return 0 if ok and all(result.passed for result in report.results) else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(argv))


if __name__ == "__main__":
    sys.exit(main())
