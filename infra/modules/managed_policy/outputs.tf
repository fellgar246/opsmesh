output "policy_engine_arn" {
  description = "ARN of the managed policy engine."
  value       = aws_bedrockagentcore_policy_engine.demo.policy_engine_arn
}

output "policy_engine_id" {
  description = "Identifier of the managed policy engine."
  value       = aws_bedrockagentcore_policy_engine.demo.policy_engine_id
}
