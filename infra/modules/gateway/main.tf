data "aws_iam_policy_document" "assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "gateway" {
  name               = "${var.name_prefix}-gateway"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

resource "aws_bedrockagentcore_gateway" "tools" {
  name        = "${var.name_prefix}-gateway"
  description = "MCP gateway for specialist tool calls. The simulator database credential is not stored here."
  role_arn    = aws_iam_role.gateway.arn

  authorizer_type = "AWS_IAM"
  protocol_type   = "MCP"

  protocol_configuration {
    mcp {
      instructions       = "Named simulator reads. This gateway does not submit purchase orders."
      supported_versions = [var.protocol_version]
    }
  }

  dynamic "policy_engine_configuration" {
    for_each = var.policy_engine_arn == null ? [] : [var.policy_engine_arn]
    content {
      arn  = policy_engine_configuration.value
      mode = "ENFORCE"
    }
  }

  tags = var.tags
}
