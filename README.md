# OpsMesh

Operations control plane for bounded, local-first workflows. The default execution profile is local deterministic simulation. Cloud agent, model, and runtime integrations stay off unless a feature flag enables them.

## Requirements

- Node.js 20 or newer
- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL 16, for the API health check against a real database

## Local quality gate

From a clean checkout, with no cloud credentials:

```bash
make check
```

The command installs dependencies, then lints, type-checks, and tests `apps/web` and `apps/api`. It does not call a cloud provider. Cloud integration tests are skipped when credentials are absent.

Copy `.env.example` to `.env` only when you want local overrides. The API starts correctly from the defaults in code, which match the example file.

## Local database

```bash
docker compose up -d postgres
```

The API health check expects `DATABASE_URL` from `.env.example`. Compose publishes PostgreSQL on port 5433 so it does not collide with another local server on 5432.

```bash
cd apps/api && uv run uvicorn opsmesh_api.main:app --reload
```

`GET /health` returns success after it can run a query on local PostgreSQL.

Load the enterprise fixtures after the schema is current. Running the loader again replaces simulator rows with the same fixtures.

```bash
cd apps/api && uv run alembic upgrade head
cd apps/api && uv run python -m opsmesh_api.services.seed
```

## Layout

| Path | Role |
| --- | --- |
| `apps/web` | Next.js operations console |
| `apps/api` | FastAPI control plane |
| `agents/shared` | Code shared by agents |
| `tools` | Tool implementations |
| `workflows` | Workflow definitions |
| `services` | Domain services |
| `knowledge/policies` | Policy documents |
| `evals` | Evaluation suites |
| `infra` | Infrastructure modules |
| `docs/adr` | Architecture decision records |

Infrastructure in this repository is a skeleton. Applying it is a separate step and is not part of the local quality gate.
