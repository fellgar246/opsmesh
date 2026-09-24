locals {
  tags = {
    Project     = "opsmesh"
    Environment = "dev"
    ManagedBy   = "terraform"
    CostCenter  = "learning"
    AutoCleanup = "true"
  }
}

resource "aws_sns_topic" "budget_alerts" {
  name = "opsmesh-dev-budget-alerts"
  tags = local.tags
}

resource "aws_budgets_budget" "monthly" {
  name         = "opsmesh-dev-monthly"
  budget_type  = "COST"
  limit_amount = "5"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  tags         = local.tags

  cost_filter {
    name = "TagKeyValue"
    values = [
      "user:Project$opsmesh",
      "user:Environment$dev",
    ]
  }

  # 20%, 60%, and 100% of 5 USD surface 1, 3, and 5 USD.
  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 20
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 60
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
  }
}
