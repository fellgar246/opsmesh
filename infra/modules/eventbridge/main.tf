data "aws_caller_identity" "current" {}

resource "aws_cloudwatch_event_bus" "domain" {
  name = var.name_prefix
  tags = var.tags
}

resource "aws_cloudwatch_event_rule" "domain" {
  name           = "${var.name_prefix}-domain"
  event_bus_name = aws_cloudwatch_event_bus.domain.name
  description    = "Domain events published by the control plane."

  event_pattern = jsonencode({
    source = ["opsmesh.domain"]
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "domain" {
  rule           = aws_cloudwatch_event_rule.domain.name
  event_bus_name = aws_cloudwatch_event_bus.domain.name
  arn            = var.queue_arn
  target_id      = "domain-queue"
}

# Scheduled rules are supported on the default bus only.
resource "aws_cloudwatch_event_rule" "schedule" {
  count               = var.enable_schedule ? 1 : 0
  name                = "${var.name_prefix}-daily"
  description         = "Optional daily tick. It does not call a model or place an order."
  schedule_expression = "rate(1 day)"
  tags                = var.tags
}

resource "aws_cloudwatch_event_target" "schedule" {
  count     = var.enable_schedule ? 1 : 0
  rule      = aws_cloudwatch_event_rule.schedule[0].name
  arn       = var.queue_arn
  target_id = "schedule-queue"

  input = jsonencode({
    event_type = "ScheduleTick"
  })
}

data "aws_iam_policy_document" "queue" {
  statement {
    effect  = "Allow"
    actions = ["sqs:SendMessage"]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    resources = [var.queue_arn]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sqs_queue_policy" "domain" {
  queue_url = var.queue_url
  policy    = data.aws_iam_policy_document.queue.json
}
