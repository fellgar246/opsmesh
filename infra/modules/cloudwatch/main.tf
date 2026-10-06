resource "aws_cloudwatch_log_group" "api" {
  name              = "/opsmesh/${var.environment}/api"
  retention_in_days = 14
  tags              = var.tags
}

resource "aws_cloudwatch_log_group" "consumer" {
  name              = "/opsmesh/${var.environment}/consumer"
  retention_in_days = 14
  tags              = var.tags
}

resource "aws_cloudwatch_log_group" "agents" {
  name              = "/opsmesh/${var.environment}/agents"
  retention_in_days = 14
  tags              = var.tags
}

resource "aws_cloudwatch_metric_alarm" "api_errors" {
  alarm_name          = "${var.name_prefix}-api-errors"
  alarm_description   = "Errors from the demo API function."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0

  dimensions = {
    FunctionName = var.api_function_name
  }

  tags = var.tags
}
