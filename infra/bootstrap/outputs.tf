output "state_bucket" {
  description = "Encrypted bucket that stores demo state."
  value       = aws_s3_bucket.state.bucket
}

output "lock_table" {
  description = "Table that locks demo state."
  value       = aws_dynamodb_table.lock.name
}

output "deploy_role_arn" {
  description = "Role to store as the AWS_DEPLOY_ROLE_ARN repository variable."
  value       = module.github_oidc.deploy_role_arn
}
