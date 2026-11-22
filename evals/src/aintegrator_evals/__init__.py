"""Eval suite: deterministic graders, trajectory checks and LLM-as-judge."""

from __future__ import annotations

from aintegrator_evals.cassette import CassetteLLM, load_cases, load_document
from aintegrator_evals.graders import DeterministicJudge, Judge, PinnedLLMJudge
from aintegrator_evals.models import Case, CaseResult, Metrics
from aintegrator_evals.runner import EvalReport, compare_baseline, run_suite

__all__ = [
    "Case",
    "CaseResult",
    "CassetteLLM",
    "DeterministicJudge",
    "EvalReport",
    "Judge",
    "Metrics",
    "PinnedLLMJudge",
    "compare_baseline",
    "load_cases",
    "load_document",
    "run_suite",
]

__version__ = "0.5.0"
