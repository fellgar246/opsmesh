output "budget_name" {
  description = "Name of the monthly cost budget."
  value       = aws_budgets_budget.monthly.name
}

output "alert_topic_arn" {
  description = "SNS topic that receives budget notifications."
  value       = aws_sns_topic.budget_alerts.arn
}

output "tags" {
  description = "Tags applied to budget resources."
  value       = local.tags
}
