output "deploy_role_arn" {
  description = "Role CI assumes through GitHub OIDC."
  value       = aws_iam_role.deploy.arn
}

output "provider_arn" {
  description = "ARN of the GitHub OIDC provider."
  value       = aws_iam_openid_connect_provider.github.arn
}
