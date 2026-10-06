data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api" {
  name               = "${var.name_prefix}-api"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

resource "aws_iam_role" "consumer" {
  name               = "${var.name_prefix}-consumer"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "api_logs" {
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      var.api_log_group_arn,
      "${var.api_log_group_arn}:*",
    ]
  }
}

data "aws_iam_policy_document" "consumer" {
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      var.consumer_log_group_arn,
      "${var.consumer_log_group_arn}:*",
    ]
  }

  statement {
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:ChangeMessageVisibility",
    ]
    resources = [var.queue_arn]
  }

  statement {
    effect = "Allow"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:GetItem",
    ]
    resources = [var.checkpoint_table_arn]
  }
}

resource "aws_iam_role_policy" "api_logs" {
  name   = "logs"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api_logs.json
}

resource "aws_iam_role_policy" "consumer" {
  name   = "consume"
  role   = aws_iam_role.consumer.id
  policy = data.aws_iam_policy_document.consumer.json
}
