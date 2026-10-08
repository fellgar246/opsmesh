"""Local checks for the demo stack defaults, tags, and deployment workflows."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def variable_block(text: str, name: str) -> str:
    match = re.search(rf'variable "{name}" \{{(.*?)\n\}}', text, re.S)
    if match is None:
        fail(f"missing variable {name}")
    return match.group(1)


def main() -> None:
    variables = (ROOT / "infra/environments/dev/variables.tf").read_text()
    for name in (
        "enable_memory",
        "enable_managed_retrieval",
        "enable_managed_policy",
        "enable_showcase",
        "enable_state_machine",
    ):
        block = variable_block(variables, name)
        if not re.search(r"default\s*=\s*false", block):
            fail(f"{name} must default to false")
    for name in (
        "enable_agent_runtimes",
        "enable_supervisor_runtime",
        "enable_gateway",
    ):
        block = variable_block(variables, name)
        if not re.search(r"default\s*=\s*true", block):
            fail(f"{name} must default to true")
    if not re.search(r'variable "environment".*?default\s*=\s*"dev"', variables, re.S):
        fail("environment must default to dev")

    budget = (ROOT / "infra/modules/budget/main.tf").read_text()
    if not re.search(r'limit_amount\s*=\s*"5"', budget) or not re.search(r'limit_unit\s*=\s*"USD"', budget):
        fail("monthly budget must be 5 USD")
    if "notification_amounts_usd = [1, 3, 5]" not in budget:
        fail("budget notifications must be 1, 3, and 5 USD")

    locals_tf = (ROOT / "infra/environments/dev/locals.tf").read_text()
    for key, value in (
        ("Project", "opsmesh"),
        ("ManagedBy", "terraform"),
        ("CostCenter", "learning"),
        ("AutoCleanup", "true"),
    ):
        if key not in locals_tf or f'"{value}"' not in locals_tf:
            fail(f"missing tag {key}={value}")
    if "Environment" not in locals_tf:
        fail("missing Environment tag")

    backend = (ROOT / "infra/environments/dev/backend.tf").read_text()
    if 'backend "s3"' not in backend or "encrypt = true" not in backend:
        fail("demo state must use an encrypted remote backend")

    oidc = (ROOT / "infra/modules/github_oidc/main.tf").read_text()
    if "token.actions.githubusercontent.com" not in oidc:
        fail("deployment identity must use GitHub OIDC")
    if "aws_access_key" in oidc or "AKIA" in oidc:
        fail("deployment identity must not use a static access key")

    workflows = ROOT / ".github/workflows"
    manual = (
        "plan.yml",
        "deploy.yml",
        "deploy-agents.yml",
        "evaluation.yml",
        "showcase.yml",
        "destroy.yml",
    )
    for name in manual:
        path = workflows / name
        if not path.is_file():
            fail(f"missing workflow {name}")
        text = path.read_text()
        if "workflow_dispatch" not in text:
            fail(f"{name} must be started manually")
        if re.search(r"^\s*pull_request\s*:", text, re.M):
            fail(f"{name} must not run on pull requests")
        if "aws-access-key-id" in text or "AWS_ACCESS_KEY" in text or "AWS_SECRET_ACCESS_KEY" in text:
            fail(f"{name} must not read a static access key")

    for name in ("plan.yml", "deploy.yml", "deploy-agents.yml", "showcase.yml", "destroy.yml"):
        text = (workflows / name).read_text()
        if "role-to-assume" not in text or "id-token: write" not in text:
            fail(f"{name} must assume a role with GitHub OIDC")
        if "environment: demo" not in text:
            fail(f"{name} must use the demo environment gate")

    quality = (workflows / "quality-gate.yml").read_text()
    if "role-to-assume" in quality or "id-token: write" in quality or "aws-access-key-id" in quality:
        fail("pull request CI must not deploy or read a static key")
    if "evaluation full" in quality or "evaluation live" in quality or "OPSMESH_LIVE_EVAL" in quality:
        fail("pull request CI must not run the full or live evaluation")
    if "terraform apply" in quality:
        fail("pull request CI must not apply infrastructure")
    if "make check" not in quality:
        fail("pull request CI must run the quality gate")

    evaluation = (workflows / "evaluation.yml").read_text()
    if "opsmesh_api.evaluation full" not in evaluation:
        fail("the evaluation workflow must run the full suite")
    if "role-to-assume" in evaluation:
        fail("the evaluation workflow must not deploy")
    if re.search(r"OPSMESH_LIVE_EVAL\s*[:=]\s*['\"]?1", evaluation):
        fail("the evaluation workflow must not enable live model evaluation")

    makefile = (ROOT / "Makefile").read_text()
    for token in (
        "check: check-web check-api check-infra",
        "tests/test_agent_contracts.py",
        "tests/test_deterministic_workflows.py",
        "tests/test_policy_retrieval.py",
        "tests/test_managed_policy.py",
        "tests/test_identity.py",
        "test_smoke_suite_passes_offline",
        "plan",
        "agent-deploy",
        "cost-check",
        "destroy",
    ):
        if token not in makefile:
            fail(f"Makefile is missing {token}")

    for path in (ROOT / "infra").rglob("*.tf"):
        text = path.read_text()
        if "AKIA" in text or "aws_secret_access_key" in text:
            fail(f"static credential in {path}")

    print("infra checks passed")


if __name__ == "__main__":
    main()
