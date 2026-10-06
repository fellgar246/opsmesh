output "policy_arn" {
  description = "Policy that allows the configured foundation model."
  value       = aws_iam_policy.invoke.arn
}

output "model_id" {
  description = "Foundation model id the policy allows."
  value       = var.model_id
}
