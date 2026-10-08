# OpsMesh

Operations control plane for bounded, local-first workflows. The default execution profile is local deterministic simulation. Cloud agent, model, and runtime integrations stay off unless a feature flag enables them.

Model-backed specialists run only when `AWS_AGENTIC_FEATURES_ENABLED` and `MODEL_PROVIDER_ENABLED` are both true. Otherwise the reorder workflow uses the local stubs, and the golden proposal stays the same. The live model provider is Amazon Bedrock. Local tests do not call it.

A specialist runs on Amazon Bedrock AgentCore Runtime when `AWS_AGENTIC_FEATURES_ENABLED` and `CLOUD_AGENT_RUNTIME_ENABLED` are both true and that specialist is named in `REMOTE_RUNTIME_AGENTS`. Specialists are selected one at a time, in the order policy, inventory, supplier, finance, then demand. The reorder coordinator still accepts only the result envelope. The runtime SDK stays inside the runtime adapter, and the outbound call uses that specialist's agent identity. The local client is used unless both flags are on and the specialist is listed. Local tests use a fake runtime transport and do not call AWS. A live runtime check runs only when `OPSMESH_LIVE_AGENT_RUNTIME=1`.

Simulator reads are named tools on a local registry. Each tool has an argument schema and an allowlist of agent types. A specialist cannot call another domain's tool, and the supervisor cannot call simulator tools. Denied and invalid calls are audited. The registry is the tool port.

Specialist tool calls use Amazon Bedrock AgentCore Gateway when `AWS_AGENTIC_FEATURES_ENABLED` and `CLOUD_TOOL_GATEWAY_ENABLED` are both true. The gateway speaks MCP and serves the same read-only schemas and agent allowlists as the local registry. A catalog check compares that schema and allowlist before any tool call and fails closed when they differ. The outbound credential is that specialist's agent identity for the tool, and the simulator database credential is not part of the specialist configuration. With either flag off, tool calls stay on the local registry and the gateway endpoint is not contacted. Local tests use a fake gateway and do not call AWS. A live gateway check runs only when `OPSMESH_LIVE_TOOL_GATEWAY=1`.

Agent and tool operations can take a second vote from Amazon Bedrock AgentCore Policy when `AWS_AGENTIC_FEATURES_ENABLED` and `CLOUD_POLICY_ENABLED` are both true. The local policy engine still decides eligibility, quantity, supplier approval, and whether a person must approve. The managed vote can deny an operation the local allowlist permits. It cannot allow an operation the local engine denies, change a proposal quantity, permit an unapproved supplier, or skip a required human approval. With either flag off, no policy client is constructed and no policy endpoint is called. If the flag is on and the adapter does not return a decision, that operation fails closed. Local tests use a fake vote and do not call AWS. A live policy check runs only when `OPSMESH_LIVE_POLICY=1`.

Draft procurement writes are local. `procurement.create_draft_purchase_order` stores one draft when a feasible proposal's SKU, supplier, quantity, and unit cost match the call, and the call carries an idempotency key. The same key and arguments return that draft. The same key with different arguments fails and leaves the draft unchanged. When approval is required, `procurement.submit_purchase_order_for_approval` advances that draft only after every required role has approved the live proposal arguments. A proposal that does not require approval stays unapproved and is parked waiting. A total above 5000 needs a procurement manager. A total above 25000 also needs a finance approver. The approval stores the arguments hash, the role, and the actor, and it expires 72 hours after it is created. A changed proposal needs a new approval. A viewer, an operations analyst, or an admin cannot take a role's decision. Submit compares the live hash with the approval hash and denies a mismatch, a rejection, or an expired pending approval. The advanced order stays in the simulator and is not sent to a supplier. Those tools belong to the procurement executor. Inventory, demand, supplier, finance, policy, and the supervisor cannot call them. When the tool gateway is on, these writes still run on the local registry.

The console lists pending approvals for the active tenant at `/approvals`. Business routes require a bearer token. The local profile signs that token with a development issuer. Amazon Cognito verifies tokens when the execution profile is not local. The token carries the tenant and the role. A viewer cannot start a workflow, and an approval uses the role on the token. A token for one tenant cannot read or approve another tenant's records. Tenant admin stays inside that tenant unless `PLATFORM_ROLE` names a separate role.

Workflow changes publish a domain event after the database commit. The event is written to an outbox in the same transaction as the state change, then a worker hands it to the bus. If the bus is down, the business change stays committed and the outbox row is retried. The local profile delivers events on an in-process bus. Consumers project an audit view, record a notification, store an analytics fact, mark a finished workflow for later evaluation, and record proposal processing. Each consumer applies one event id once, so a repeated `DraftPOCreated` does not create another draft. The audit view for the active tenant is at `/audit`. Amazon EventBridge publishes domain events and Amazon SQS delivers them only when `AWS_AGENTIC_FEATURES_ENABLED` and `CLOUD_EVENT_BUS_ENABLED` are both true. Otherwise no cloud endpoint is called.

Demand forecasts are read through a provider port. The default provider is the local fixture, so the golden reorder for `ABC-42` stays at a P90 of 345 and a proposal quantity of 250. Set `FORECAST_PROVIDER=demandpilot` to read quantiles from a DemandPilot deployment instead. The adapter maps that product's document into the forecast reading, records the provider name and source record on the evidence, and translates tenant ids through `DEMANDPILOT_TENANT_MAP`. An unlisted tenant is not sent under its OpsMesh id. If DemandPilot does not respond, or the document has no P90, the reorder fails closed and does not substitute the fixture. A cached reading is used only when `DEMANDPILOT_FRESHNESS_SECONDS` is set and that tenant's cached value is still inside the window. The window is blank by default. Workflow code depends on the port. The DemandPilot client stays in the adapter.

After a proposal exists, the approval wait is a deterministic state machine. Agent planning has already finished. The machine calls the approval service and the draft service. A matching approval creates one draft and publishes a notification on the bus. A rejection leaves the draft uncreated; `ApprovalRejected` is already recorded. An expiry uses the approval lifetime of 72 hours, leaves the draft uncreated, and marks the proposal `EXPIRED`. The same approve signal a second time does not create another draft. Before the draft is written, the machine reads the live arguments hash and stops when that hash differs from the approved hash. The local profile runs these transitions on the in-process bus. Amazon Step Functions runs the same states only when `AWS_AGENTIC_FEATURES_ENABLED` and `CLOUD_STATE_MACHINE_ENABLED` are both true. With either flag off, no state-machine client is constructed.

Evaluation scores workflow changes against the golden workflows in `evals`. The default gate runs the pinned smoke subset on the local engine and does not call a hosted model. `python -m opsmesh_api.evaluation full` scores every golden workflow, using recorded traces for integration cases. Live model evaluation runs only when `OPSMESH_LIVE_EVAL=1`, and this profile still does not call a hosted model. The latest scores for the active tenant are at `/evaluations`. An optional fixture sink is constructed only when `AWS_AGENTIC_FEATURES_ENABLED` and `HOSTED_EVALUATION_ENABLED` are both true. It does not replace the local scorer. When a previous evaluation exists, the console shows the model-call and estimated-cost difference.

A reorder skips finance and supplier when available stock is already above the selected forecast. Those tasks are stored as skipped, and the proposal quantity is 0. Each tenant can start 15 workflows per UTC day; the next start is rejected. Estimated cost uses the checked-in price table, and a model that ran without a listed price is unknown. Tool results can be reused inside one workflow when the tenant, tool, and arguments match. `AWS_AGENTIC_FEATURES_ENABLED=false` sends the next specialist call to local deterministic simulation. The cost view for the active tenant is at `/cost`. Budget alerts at 1, 3, and 5 USD remain infrastructure signals.

The supervisor turns a request into a validated task graph and stores that plan on the workflow. It selects allowlisted specialists and does not query simulator data. Local runs use a deterministic planner. A model planner is used only when the model provider flag is on, and its output passes the same validator. More than four supervisor iterations fails the run.

The coordinator executes that graph. Tasks with no unfinished dependencies start together, and a task starts only after every dependency has completed. A failed specialist leaves sibling results in place, skips dependents, and does not build a purchase from invented supplier data. Each task times out, retries at most twice, and the workflow itself stops at 180 seconds. Model-call and tool-call ceilings are checked before the next task starts.

When specialist numbers disagree, the reorder rules produce the feasible quantity. The proposal keeps those numbers as evidence and records which constraints changed the requested quantity. Approval roles come from the policy rules. An explanation is kept only when every stated quantity and approval requirement matches that result; otherwise it is replaced. A new proposal supersedes the previous draft, so a run has one active proposal. Budget plans still fund the higher stockout risk first.

The policy agent explains that decision with citations from the versioned documents in `knowledge/policies`. Search keeps passages for the task tenant that are inside their effective dates, and it returns at most five. `policy.search` returns those citations. It does not set approval or block flags. If nothing is in scope, the decision stands and the result warns `policy_citation_missing`. A passage that denies a required approval is returned as a warning, and the flags stay with the policy rules.

## Requirements

- Node.js 20 or newer
- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- Terraform 1.6 or newer, for the infrastructure check
- PostgreSQL 16, for the API health check against a real database

## Local quality gate

From a clean checkout, with no cloud credentials:

```bash
make check
```

The command installs dependencies, then lints, type-checks, and tests `apps/web` and `apps/api`. It also formats and validates the Terraform stack. It does not call a cloud provider or apply infrastructure. Cloud integration tests are skipped when credentials are absent. Pull-request CI runs the same gate and a security scan. It does not deploy agents or run live model evaluation.

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

Open `http://localhost:3000`. The local session uses tenant `northwind` and the operations analyst identity. The sidebar can switch that signed-in session when you need to approve or reject a proposal. The console sends the bearer token and does not send anonymous identity headers.

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

## Demo environment

The demo stack is Terraform. A default plan creates the budget, storage, event bus, identity pool, HTTP API, specialist runtimes, supervisor runtime, and tool gateway. It does not create memory, managed retrieval, managed policy, the daily schedule, or the approval state machine. The monthly budget is 5 USD, with notifications at 1, 3, and 5 USD.

Bootstrap once with your own credentials. That creates the encrypted state bucket, the lock table, and the GitHub OIDC deploy role:

```bash
export AWS_REGION=us-east-1
export TF_VAR_aws_region=us-east-1
export TF_VAR_state_bucket_name=your-unique-bucket
export TF_VAR_github_repository=owner/name
make bootstrap
```

Store the `deploy_role_arn` output as the GitHub variable `AWS_DEPLOY_ROLE_ARN`. Also set `AWS_REGION`, `TF_STATE_BUCKET`, and `TF_LOCK_TABLE`. Manual workflows assume that role through GitHub OIDC. They do not read a static access key.

```bash
export AWS_REGION=us-east-1
export TF_STATE_BUCKET=your-unique-bucket
export TF_LOCK_TABLE=opsmesh-dev-locks
make plan
AUTO_APPROVE=1 make deploy
make smoke
make cost-check
AUTO_APPROVE=1 make destroy
```

`make agent-deploy` applies the specialist, supervisor, and gateway runtimes. Showcase modules stay off until you run `AUTO_APPROVE=1 infra/scripts/terraform.sh showcase`. `make smoke` calls `GET /health` and the read-only finance budget path. It does not submit a purchase order. Running destroy again after the stack is gone succeeds. Destroy does not delete the state bucket or the deploy role.

Copy stack outputs into `EVENT_BUS_NAME`, `EVENT_QUEUE_URL`, `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`, `AGENT_RUNTIME_ENDPOINTS`, `TOOL_GATEWAY_ENDPOINT`, and `STATE_MACHINE_ARN` when the API should use the demo. Details are in `infra/README.md`.
