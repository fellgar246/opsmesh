data "archive_file" "approval" {
  type        = "zip"
  source_file = "${path.module}/../../functions/approval_process/handler.py"
  output_path = "${path.module}/.build/approval.zip"
}

resource "aws_cloudwatch_log_group" "approval" {
  name              = "/opsmesh/${var.name_prefix}/approval-wait"
  retention_in_days = 14
  tags              = var.tags
}

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

resource "aws_iam_role" "approval" {
  name               = "${var.name_prefix}-approval-wait"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "approval_logs" {
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      aws_cloudwatch_log_group.approval.arn,
      "${aws_cloudwatch_log_group.approval.arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "approval_logs" {
  name   = "logs"
  role   = aws_iam_role.approval.id
  policy = data.aws_iam_policy_document.approval_logs.json
}

resource "aws_lambda_function" "approval" {
  function_name    = "${var.name_prefix}-approval-wait"
  role             = aws_iam_role.approval.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.approval.output_path
  source_code_hash = data.archive_file.approval.output_base64sha256
  timeout          = 30
  memory_size      = 256

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.approval.name
  }

  tags = var.tags
}

data "aws_iam_policy_document" "states_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "states" {
  name               = "${var.name_prefix}-approval-wait-states"
  assume_role_policy = data.aws_iam_policy_document.states_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "states" {
  statement {
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.approval.arn]
  }
}

resource "aws_iam_role_policy" "states" {
  name   = "invoke"
  role   = aws_iam_role.states.id
  policy = data.aws_iam_policy_document.states.json
}

resource "aws_sfn_state_machine" "approval" {
  name     = "${var.name_prefix}-approval-wait"
  role_arn = aws_iam_role.states.arn
  type     = "STANDARD"
  definition = templatefile("${path.module}/../../../workflows/approval_wait.asl.json", {
    handler_arn = aws_lambda_function.approval.arn
    task_token  = "$$.Task.Token"
  })
  tags = var.tags
}

data "aws_iam_policy_document" "callback" {
  statement {
    effect = "Allow"
    actions = [
      "states:SendTaskSuccess",
      "states:SendTaskFailure",
    ]
    resources = [aws_sfn_state_machine.approval.arn]
  }
}

resource "aws_iam_role_policy" "callback" {
  name   = "callback"
  role   = aws_iam_role.approval.id
  policy = data.aws_iam_policy_document.callback.json
}
