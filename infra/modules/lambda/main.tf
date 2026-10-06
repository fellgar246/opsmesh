data "archive_file" "api" {
  type        = "zip"
  source_file = "${path.module}/../../functions/api/handler.py"
  output_path = "${path.module}/.build/api.zip"
}

data "archive_file" "consumer" {
  type        = "zip"
  source_file = "${path.module}/../../functions/consumer/handler.py"
  output_path = "${path.module}/.build/consumer.zip"
}

resource "aws_lambda_function" "api" {
  function_name    = "${var.name_prefix}-api"
  role             = var.api_role_arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.api.output_path
  source_code_hash = data.archive_file.api.output_base64sha256
  timeout          = 10
  memory_size      = 256

  logging_config {
    log_format = "Text"
    log_group  = var.api_log_group
  }

  tags = var.tags
}

resource "aws_lambda_function" "consumer" {
  function_name    = "${var.name_prefix}-consumer"
  role             = var.consumer_role_arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.consumer.output_path
  source_code_hash = data.archive_file.consumer.output_base64sha256
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      CHECKPOINT_TABLE = var.checkpoint_table_name
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = var.consumer_log_group
  }

  tags = var.tags
}

resource "aws_lambda_event_source_mapping" "events" {
  event_source_arn = var.queue_arn
  function_name    = aws_lambda_function.consumer.arn
  batch_size       = 5
  enabled          = true
}
