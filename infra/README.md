# Infrastructure

Terraform for the demo account. Resources are tagged `Project=opsmesh`, `Environment=dev`, `ManagedBy=terraform`, `CostCenter=learning`, and `AutoCleanup=true`.

PostgreSQL remains the system of record for workflows. DynamoDB stores event-consumer checkpoints. S3 stores runtime packages. The HTTP API exposes `GET /health` and `GET /tenants/{tenant_id}/finance/budget`. It does not submit purchase orders.

## Default plan

A plan with the default variables creates:

- a 5 USD monthly budget with notifications at 1, 3, and 5 USD
- the artifact bucket, checkpoint table, domain queue, event bus, user pool, log groups, and HTTP API
- model-invoke permission for the configured foundation model
- specialist runtimes, the supervisor runtime, and the tool gateway

It does not create memory, the knowledge base, the managed policy engine, or the daily schedule. Those turn on only when `enable_memory`, `enable_managed_retrieval`, `enable_managed_policy`, and `enable_showcase` are true.

## State and identity

`infra/bootstrap` creates the encrypted state bucket, the lock table, and the GitHub OIDC deploy role. Its state stays on the operator machine and is not committed. The demo stack in `infra/environments/dev` uses that remote backend with encryption enabled. Bucket name, region, and lock table come from the environment, not from a committed secret.

The deploy role trusts `repo:OWNER/NAME:environment:demo`. Pull-request CI does not assume it. Plan, deploy, agent deploy, showcase, and destroy are manual workflows, and each one sets the `demo` environment so the trust policy matches.

## Commands

```bash
export AWS_REGION=us-east-1
export TF_VAR_aws_region=us-east-1
export TF_VAR_state_bucket_name=your-unique-bucket
export TF_VAR_github_repository=owner/name
make bootstrap
```

Use the bootstrap outputs for the later commands:

```bash
export TF_STATE_BUCKET=your-unique-bucket
export TF_LOCK_TABLE=opsmesh-dev-locks
make plan
AUTO_APPROVE=1 make deploy
AUTO_APPROVE=1 make agent-deploy
make smoke
make cost-check
AUTO_APPROVE=1 make destroy
```

`make check` formats and validates both roots without cloud credentials. `OPSMESH_BASE_URL` selects the smoke target and defaults to the local API. Point it at the HTTP API output to smoke the deployed probe. Destroy is safe to repeat after the resources are already gone.

`AUTO_APPROVE=1 infra/scripts/terraform.sh showcase` enables memory, managed retrieval, managed policy, and the daily schedule in the same state. Destroy removes those resources too. It does not remove the state bucket or the deploy role.
