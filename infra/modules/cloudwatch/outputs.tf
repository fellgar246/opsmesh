output "api_log_group_name" {
  description = "Log group for the API function."
  value       = aws_cloudwatch_log_group.api.name
}

output "api_log_group_arn" {
  description = "ARN of the API log group."
  value       = aws_cloudwatch_log_group.api.arn
}

output "consumer_log_group_name" {
  description = "Log group for the event consumer."
  value       = aws_cloudwatch_log_group.consumer.name
}

output "consumer_log_group_arn" {
  description = "ARN of the consumer log group."
  value       = aws_cloudwatch_log_group.consumer.arn
}

output "agents_log_group_name" {
  description = "Log group reserved for agent runtime output."
  value       = aws_cloudwatch_log_group.agents.name
}

output "agents_log_group_arn" {
  description = "ARN of the agent log group."
  value       = aws_cloudwatch_log_group.agents.arn
}
