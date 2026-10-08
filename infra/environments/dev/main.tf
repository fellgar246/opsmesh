module "budget" {
  source      = "../../modules/budget"
  name_prefix = local.name_prefix
  environment = var.environment
  tags        = local.tags
}

module "s3" {
  source      = "../../modules/s3"
  name_prefix = local.name_prefix
  tags        = local.tags
}

module "dynamodb" {
  source      = "../../modules/dynamodb"
  name_prefix = local.name_prefix
  tags        = local.tags
}

module "sqs" {
  source      = "../../modules/sqs"
  name_prefix = local.name_prefix
  tags        = local.tags
}

module "cloudwatch" {
  source            = "../../modules/cloudwatch"
  name_prefix       = local.name_prefix
  environment       = var.environment
  api_function_name = "${local.name_prefix}-api"
  tags              = local.tags
}

module "iam" {
  source                 = "../../modules/iam"
  name_prefix            = local.name_prefix
  queue_arn              = module.sqs.queue_arn
  checkpoint_table_arn   = module.dynamodb.table_arn
  api_log_group_arn      = module.cloudwatch.api_log_group_arn
  consumer_log_group_arn = module.cloudwatch.consumer_log_group_arn
  tags                   = local.tags
}

module "lambda" {
  source                = "../../modules/lambda"
  name_prefix           = local.name_prefix
  api_role_arn          = module.iam.api_role_arn
  consumer_role_arn     = module.iam.consumer_role_arn
  api_log_group         = module.cloudwatch.api_log_group_name
  consumer_log_group    = module.cloudwatch.consumer_log_group_name
  queue_arn             = module.sqs.queue_arn
  checkpoint_table_name = module.dynamodb.table_name
  tags                  = local.tags
}

module "apigateway" {
  source              = "../../modules/apigateway"
  name_prefix         = local.name_prefix
  function_name       = module.lambda.api_function_name
  function_invoke_arn = module.lambda.api_invoke_arn
  tags                = local.tags
}

module "eventbridge" {
  source          = "../../modules/eventbridge"
  name_prefix     = local.name_prefix
  queue_arn       = module.sqs.queue_arn
  queue_url       = module.sqs.queue_url
  enable_schedule = var.enable_showcase
  tags            = local.tags
}

module "cognito" {
  source      = "../../modules/cognito"
  name_prefix = local.name_prefix
  tags        = local.tags
}

module "model_provider" {
  source      = "../../modules/model_provider"
  name_prefix = local.name_prefix
  aws_region  = var.aws_region
  model_id    = var.model_id
  tags        = local.tags
}

module "specialist_runtime" {
  count  = var.enable_agent_runtimes ? 1 : 0
  source = "../../modules/specialist_runtime"

  name_prefix         = local.name_prefix
  agents              = var.specialist_agents
  artifact_bucket     = module.s3.bucket_name
  artifact_bucket_arn = module.s3.bucket_arn
  model_policy_arn    = module.model_provider.policy_arn
  log_group_arn       = module.cloudwatch.agents_log_group_arn
  tags                = local.tags
}

module "supervisor_runtime" {
  count  = var.enable_supervisor_runtime ? 1 : 0
  source = "../../modules/supervisor_runtime"

  name_prefix         = local.name_prefix
  artifact_bucket     = module.s3.bucket_name
  artifact_bucket_arn = module.s3.bucket_arn
  model_policy_arn    = module.model_provider.policy_arn
  log_group_arn       = module.cloudwatch.agents_log_group_arn
  tags                = local.tags
}

module "managed_policy" {
  count       = var.enable_managed_policy ? 1 : 0
  source      = "../../modules/managed_policy"
  name_prefix = local.name_prefix
  tags        = local.tags
}

module "gateway" {
  count  = var.enable_gateway ? 1 : 0
  source = "../../modules/gateway"

  name_prefix       = local.name_prefix
  protocol_version  = var.gateway_protocol_version
  policy_engine_arn = one(module.managed_policy[*].policy_engine_arn)
  tags              = local.tags
}

module "memory" {
  count       = var.enable_memory ? 1 : 0
  source      = "../../modules/memory"
  name_prefix = local.name_prefix
  tags        = local.tags
}

module "knowledge_base" {
  count       = var.enable_managed_retrieval ? 1 : 0
  source      = "../../modules/knowledge_base"
  name_prefix = local.name_prefix
  aws_region  = var.aws_region
  tags        = local.tags
}

module "state_machine" {
  count       = var.enable_state_machine ? 1 : 0
  source      = "../../modules/state_machine"
  name_prefix = local.name_prefix
  tags        = local.tags
}
