output "gateway_url" {
  description = "URL of the tool gateway."
  value       = aws_bedrockagentcore_gateway.tools.gateway_url
}

output "gateway_arn" {
  description = "ARN of the tool gateway."
  value       = aws_bedrockagentcore_gateway.tools.gateway_arn
}

output "role_name" {
  description = "Execution role for the gateway."
  value       = aws_iam_role.gateway.name
}
