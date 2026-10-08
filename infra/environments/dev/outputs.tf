output "api_endpoint" {
  description = "Base URL of the demo HTTP API."
  value       = module.apigateway.api_endpoint
}

output "event_bus_name" {
  description = "Event bus name for EVENT_BUS_NAME."
  value       = module.eventbridge.bus_name
}

output "event_queue_url" {
  description = "Queue URL for EVENT_QUEUE_URL."
  value       = module.sqs.queue_url
}

output "cognito_user_pool_id" {
  description = "User pool id for COGNITO_USER_POOL_ID."
  value       = module.cognito.user_pool_id
}

output "cognito_client_id" {
  description = "App client id for COGNITO_CLIENT_ID."
  value       = module.cognito.client_id
}

output "budget_name" {
  description = "Monthly cost budget."
  value       = module.budget.budget_name
}

output "agent_runtime_endpoints" {
  description = "agent=arn pairs for AGENT_RUNTIME_ENDPOINTS. Null when specialist runtimes are off."
  value       = one(module.specialist_runtime[*].endpoints)
}

output "supervisor_runtime_arn" {
  description = "Supervisor runtime ARN. Null when the supervisor runtime is off."
  value       = one(module.supervisor_runtime[*].runtime_arn)
}

output "gateway_url" {
  description = "Tool gateway URL for TOOL_GATEWAY_ENDPOINT. Null when the gateway is off."
  value       = one(module.gateway[*].gateway_url)
}

output "state_machine_arn" {
  description = "Approval wait state machine for STATE_MACHINE_ARN. Null when the machine is off."
  value       = one(module.state_machine[*].state_machine_arn)
}

output "optional_modules" {
  description = "Showcase modules. A default plan leaves every value false."
  value = {
    memory            = var.enable_memory
    managed_retrieval = var.enable_managed_retrieval
    managed_policy    = var.enable_managed_policy
    showcase          = var.enable_showcase
    state_machine     = var.enable_state_machine
  }
}
