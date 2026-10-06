.PHONY: check check-web check-api check-infra security plan deploy agent-deploy smoke cost-check destroy bootstrap eval-full

# Local quality gate. Lint, type checks, tests, and infrastructure validation.
# Does not call a cloud provider. Cloud integration tests skip without credentials.
check: check-web check-api check-infra

check-web:
	npm --prefix apps/web ci
	npm --prefix apps/web run lint
	npm --prefix apps/web run typecheck
	npm --prefix apps/web test

check-api:
	cd apps/api && uv sync --frozen
	cd apps/api && uv run ruff check .
	cd apps/api && uv run mypy
	cd apps/api && uv run pytest tests/test_agent_contracts.py
	cd apps/api && uv run pytest tests/test_deterministic_workflows.py
	cd apps/api && uv run pytest tests/test_policy_retrieval.py tests/test_managed_policy.py
	cd apps/api && uv run pytest tests/test_identity.py
	cd apps/api && uv run pytest tests/test_evaluation.py::test_smoke_suite_passes_offline
	cd apps/api && uv run pytest \
		--ignore=tests/test_agent_contracts.py \
		--ignore=tests/test_deterministic_workflows.py \
		--ignore=tests/test_policy_retrieval.py \
		--ignore=tests/test_managed_policy.py \
		--ignore=tests/test_identity.py \
		--deselect tests/test_evaluation.py::test_smoke_suite_passes_offline

check-infra:
	python3 infra/scripts/check_infra.py
	infra/scripts/terraform.sh fmt
	infra/scripts/terraform.sh validate

security:
	trivy fs --scanners secret,misconfig --severity HIGH,CRITICAL --exit-code 1 --skip-dirs node_modules,.venv,.terraform,.build .

plan:
	infra/scripts/terraform.sh plan

deploy:
	infra/scripts/terraform.sh apply

agent-deploy:
	infra/scripts/terraform.sh agent-deploy

smoke:
	infra/scripts/smoke.sh

cost-check:
	infra/scripts/cost.sh

destroy:
	infra/scripts/terraform.sh destroy

bootstrap:
	infra/scripts/terraform.sh bootstrap

# Score every golden workflow on the local engine and recorded traces.
eval-full:
	cd apps/api && uv run python -m opsmesh_api.evaluation full
