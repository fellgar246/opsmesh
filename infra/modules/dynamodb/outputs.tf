output "table_name" {
  description = "Checkpoint table used by the event consumer."
  value       = aws_dynamodb_table.checkpoints.name
}

output "table_arn" {
  description = "ARN of the checkpoint table."
  value       = aws_dynamodb_table.checkpoints.arn
}
