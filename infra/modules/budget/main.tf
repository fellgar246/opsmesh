locals {
  # Absolute thresholds on the 5 USD monthly budget.
  notification_amounts_usd = [1, 3, 5]
}

resource "aws_sns_topic" "budget_alerts" {
  name = "${var.name_prefix}-budget-alerts"
  tags = var.tags
}

resource "aws_budgets_budget" "monthly" {
  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = "5"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  tags         = var.tags

  cost_filter {
    name = "TagKeyValue"
    values = [
      "user:Project$opsmesh",
      format("user:Environment$%s", var.environment),
    ]
  }

  dynamic "notification" {
    for_each = toset(local.notification_amounts_usd)
    content {
      comparison_operator       = "GREATER_THAN"
      threshold                 = notification.value
      threshold_type            = "ABSOLUTE"
      notification_type         = "ACTUAL"
      subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
    }
  }
}
