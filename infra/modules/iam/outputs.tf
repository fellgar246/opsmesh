output "api_role_arn" {
  description = "Execution role for the API function."
  value       = aws_iam_role.api.arn
}

output "consumer_role_arn" {
  description = "Execution role for the event consumer."
  value       = aws_iam_role.consumer.arn
}
