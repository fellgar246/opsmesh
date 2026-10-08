#!/usr/bin/env bash
# Plan, apply, and destroy the demo stack. CI passes backend settings as variables.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_DIR="${ROOT}/infra/environments/dev"
BOOTSTRAP_DIR="${ROOT}/infra/bootstrap"

init_remote() {
  : "${AWS_REGION:?Set AWS_REGION to the demo region.}"
  : "${TF_STATE_BUCKET:?Set TF_STATE_BUCKET to the remote state bucket.}"
  : "${TF_LOCK_TABLE:?Set TF_LOCK_TABLE to the state lock table.}"
  export TF_VAR_aws_region="${TF_VAR_aws_region:-${AWS_REGION}}"
  terraform -chdir="${ENV_DIR}" init -input=false -reconfigure \
    -backend-config="bucket=${TF_STATE_BUCKET}" \
    -backend-config="region=${AWS_REGION}" \
    -backend-config="dynamodb_table=${TF_LOCK_TABLE}" \
    -backend-config="encrypt=true"
}

approve_args() {
  if [[ "${AUTO_APPROVE:-}" == "1" ]]; then
    printf '%s\n' -auto-approve
  fi
}

validate_dir() {
  local dir="$1"
  terraform -chdir="${dir}" init -backend=false -input=false
  terraform -chdir="${dir}" validate
}

command="${1:-}"
case "${command}" in
  fmt)
    terraform fmt -check -recursive -diff "${ROOT}/infra"
    ;;
  validate)
    validate_dir "${ENV_DIR}"
    validate_dir "${BOOTSTRAP_DIR}"
    ;;
  init)
    init_remote
    ;;
  plan)
    init_remote
    terraform -chdir="${ENV_DIR}" plan -input=false -lock-timeout=2m
    ;;
  apply)
    init_remote
    # shellcheck disable=SC2046
    terraform -chdir="${ENV_DIR}" apply -input=false -lock-timeout=2m $(approve_args)
    ;;
  agent-deploy)
    export TF_VAR_enable_agent_runtimes=true
    export TF_VAR_enable_supervisor_runtime=true
    export TF_VAR_enable_gateway=true
    init_remote
    # shellcheck disable=SC2046
    terraform -chdir="${ENV_DIR}" apply -input=false -lock-timeout=2m $(approve_args)
    ;;
  showcase)
    export TF_VAR_enable_memory=true
    export TF_VAR_enable_managed_retrieval=true
    export TF_VAR_enable_managed_policy=true
    export TF_VAR_enable_showcase=true
    export TF_VAR_enable_state_machine=true
    init_remote
    # shellcheck disable=SC2046
    terraform -chdir="${ENV_DIR}" apply -input=false -lock-timeout=2m $(approve_args)
    ;;
  destroy)
    init_remote
    # shellcheck disable=SC2046
    terraform -chdir="${ENV_DIR}" destroy -input=false -lock-timeout=2m $(approve_args)
    ;;
  bootstrap)
    : "${TF_VAR_state_bucket_name:?Set TF_VAR_state_bucket_name to a globally unique bucket name.}"
    : "${TF_VAR_github_repository:?Set TF_VAR_github_repository to owner/name.}"
    export TF_VAR_aws_region="${TF_VAR_aws_region:-${AWS_REGION:-us-east-1}}"
    terraform -chdir="${BOOTSTRAP_DIR}" init -input=false
    # shellcheck disable=SC2046
    terraform -chdir="${BOOTSTRAP_DIR}" apply -input=false $(approve_args)
    ;;
  *)
    echo "Usage: terraform.sh fmt|validate|init|plan|apply|agent-deploy|showcase|destroy|bootstrap" >&2
    exit 2
    ;;
esac
