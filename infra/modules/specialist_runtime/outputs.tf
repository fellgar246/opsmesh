output "role_name" {
  description = "Execution role shared by specialist runtimes."
  value       = aws_iam_role.specialist.name
}

output "endpoints" {
  description = "agent=arn pairs in conversion order, for the control-plane endpoint setting."
  value = join(",", [
    for agent in var.agents : "${agent}=${aws_bedrockagentcore_agent_runtime.specialist[agent].agent_runtime_arn}"
  ])
}

output "runtime_arns" {
  description = "Runtime ARN by specialist."
  value = {
    for agent in var.agents : agent => aws_bedrockagentcore_agent_runtime.specialist[agent].agent_runtime_arn
  }
}
