# OpsMesh

Operations control plane for bounded, local-first workflows. The default execution profile is local deterministic simulation. Cloud agent, model, and runtime integrations stay off unless a feature flag enables them.

Model-backed specialists run only when `AWS_AGENTIC_FEATURES_ENABLED` and `MODEL_PROVIDER_ENABLED` are both true. Otherwise the reorder workflow uses the local stubs, and the golden proposal stays the same. The live model provider is Amazon Bedrock. Local tests do not call it.

Simulator reads are named tools on a local registry. Each tool has an argument schema and an allowlist of agent types. A specialist cannot call another domain's tool, and the supervisor cannot call simulator tools. Denied and invalid calls are audited. The registry is the port a later remote gateway can implement.

The supervisor turns a request into a validated task graph and stores that plan on the workflow. It selects allowlisted specialists and does not query simulator data. Local runs use a deterministic planner. A model planner is used only when the model provider flag is on, and its output passes the same validator. More than four supervisor iterations fails the run.

The coordinator executes that graph. Tasks with no unfinished dependencies start together, and a task starts only after every dependency has completed. A failed specialist leaves sibling results in place, skips dependents, and does not build a purchase from invented supplier data. Each task times out, retries at most twice, and the workflow itself stops at 180 seconds. Model-call and tool-call ceilings are checked before the next task starts.

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

The operations console runs separately and calls that API:

```bash
cd apps/web && npm run dev
```

Open `http://localhost:3000`. The local session uses tenant `northwind` and the operations analyst identity.

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
