"""Static checks for the fictional AWS/Terraform layout (no services or Terraform CLI)."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TF_ROOT = REPO_ROOT / "infra" / "terraform"
DEPLOYMENT_DOC = REPO_ROOT / "docs" / "DEPLOYMENT.md"

EXPECTED_FILES = [
    TF_ROOT / "versions.tf",
    TF_ROOT / "provider.tf",
    TF_ROOT / "variables.tf",
    TF_ROOT / "locals.tf",
    TF_ROOT / "network.tf",
    TF_ROOT / "security.tf",
    TF_ROOT / "iam.tf",
    TF_ROOT / "ecr.tf",
    TF_ROOT / "logs.tf",
    TF_ROOT / "ecs.tf",
    TF_ROOT / "otel.tf",
    TF_ROOT / "step_functions.tf",
    TF_ROOT / "outputs.tf",
    TF_ROOT / "step_functions.asl.json.tftpl",
    TF_ROOT / "terraform.tfvars.example",
    TF_ROOT / "README.md",
    TF_ROOT / "lambda" / "bridge.py",
    TF_ROOT / "otel-collector" / "Dockerfile",
    REPO_ROOT / "infra" / "otel" / "collector-config.yaml",
    DEPLOYMENT_DOC,
]

ACCOUNT_ID_RE = re.compile(r"(?<!\d)(\d{12})(?!\d)")
SECRET_ASSIGNMENT_RE = re.compile(r"(?i)\b(?:password|api[_-]?key|secret|token)\s*=\s*\"[^\"$]")
PASSWORD_KEY_RE = re.compile(r"(?im)^\s*(?:[A-Z0-9_]*_?PASSWORD|password)\s*=")


TEXT_SUFFIXES = {".tf", ".tftpl", ".tfvars", ".example", ".md", ".py", ".yaml", ".yml", ".hcl"}


def _terraform_files():
    return sorted(
        path
        for path in TF_ROOT.rglob("*")
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(TF_ROOT).parts)
        and (path.suffix in TEXT_SUFFIXES or path.name == "Dockerfile")
    )


def _read(path):
    return path.read_text(encoding="utf-8")


def test_expected_layout_files_exist():
    for path in EXPECTED_FILES:
        assert path.is_file(), f"missing expected file: {path.relative_to(REPO_ROOT)}"


def test_key_resource_declarations_present():
    hcl = "\n".join(_read(path) for path in _terraform_files() if path.suffix == ".tf")
    all_text = hcl + "\n" + _read(TF_ROOT / "step_functions.asl.json.tftpl")

    assert 'resource "aws_ecs_service"' in hcl
    assert 'resource "aws_sfn_state_machine"' in hcl
    assert "waitForTaskToken" in all_text
    assert '"$$.Task.Token"' in all_text
    assert "otlp" in all_text.lower() and "xray" in all_text.lower()
    assert "awsxray" in all_text
    assert "aws_otel" in all_text or 'resource "aws_ssm_parameter" "otel_config"' in hcl
    assert 'resource "aws_sqs_queue"' in hcl
    assert 'resource "aws_cloudwatch_log_group"' in hcl
    assert 'name = "OTEL_ENABLED"' in hcl and 'value = "true"' in hcl
    assert '"ecs:DescribeTasks"' in hcl


def test_no_real_aws_account_ids():
    for path in _terraform_files():
        for match in ACCOUNT_ID_RE.finditer(_read(path)):
            assert match.group(1) == "0" * 12, (
                f"non-placeholder account ID in {path.relative_to(REPO_ROOT)}: {match.group(1)}"
            )


def test_no_inline_secret_assignments():
    for path in _terraform_files():
        for match in SECRET_ASSIGNMENT_RE.finditer(_read(path)):
            raise AssertionError(
                f"literal secret assignment in {path.relative_to(REPO_ROOT)}: {match.group(0)}"
            )


def test_tfvars_example_has_no_password_key():
    tfvars = _read(TF_ROOT / "terraform.tfvars.example")
    assert PASSWORD_KEY_RE.search(tfvars) is None
