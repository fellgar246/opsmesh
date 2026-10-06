output "role_name" {
  description = "Execution role for the supervisor runtime."
  value       = aws_iam_role.supervisor.name
}

output "runtime_arn" {
  description = "ARN of the supervisor runtime."
  value       = aws_bedrockagentcore_agent_runtime.supervisor.agent_runtime_arn
}
