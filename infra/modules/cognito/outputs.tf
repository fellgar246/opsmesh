output "user_pool_id" {
  description = "User pool the control plane verifies when the profile is not local."
  value       = aws_cognito_user_pool.demo.id
}

output "client_id" {
  description = "App client id for the console."
  value       = aws_cognito_user_pool_client.console.id
}

output "roles" {
  description = "Group names created on the user pool."
  value       = [for group in aws_cognito_user_group.roles : group.name]
}
