resource "aws_sqs_queue" "dead_letter" {
  name                      = "${var.name_prefix}-events-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  tags                      = var.tags
}

resource "aws_sqs_queue" "events" {
  name                       = "${var.name_prefix}-events"
  message_retention_seconds  = 345600
  sqs_managed_sse_enabled    = true
  visibility_timeout_seconds = 60

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter.arn
    maxReceiveCount     = 3
  })

  tags = var.tags
}
