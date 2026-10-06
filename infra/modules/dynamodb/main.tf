resource "aws_dynamodb_table" "checkpoints" {
  name         = "${var.name_prefix}-event-checkpoints"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_id"

  attribute {
    name = "event_id"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }

  point_in_time_recovery {
    enabled = false
  }

  tags = var.tags
}
