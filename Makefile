.PHONY: check check-web check-api eval-full

# Local quality gate. Lint, type checks, and unit tests for the web app and the API.
# Does not call a cloud provider. Cloud integration tests skip without credentials.
check: check-web check-api

check-web:
	npm --prefix apps/web ci
	npm --prefix apps/web run lint
	npm --prefix apps/web run typecheck
	npm --prefix apps/web test

check-api:
	cd apps/api && uv sync --frozen
	cd apps/api && uv run ruff check .
	cd apps/api && uv run mypy
	cd apps/api && uv run pytest

# Score every golden workflow on the local engine and recorded traces.
eval-full:
	cd apps/api && uv run python -m opsmesh_api.evaluation full
