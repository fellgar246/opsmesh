data "archive_file" "package" {
  type        = "zip"
  source_file = "${path.module}/../../functions/specialist/main.py"
  output_path = "${path.module}/.build/specialist.zip"
}

resource "aws_s3_object" "package" {
  bucket      = var.artifact_bucket
  key         = "agents/specialist.zip"
  source      = data.archive_file.package.output_path
  source_hash = data.archive_file.package.output_md5
  tags        = var.tags
}

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

resource "aws_iam_role" "specialist" {
  name               = "${var.name_prefix}-specialist"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "runtime" {
  statement {
    effect = "Allow"
    actions = [
      "s3:GetObject",
    ]
    resources = ["${var.artifact_bucket_arn}/agents/*"]
  }

  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      var.log_group_arn,
      "${var.log_group_arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "runtime" {
  name   = "runtime"
  role   = aws_iam_role.specialist.id
  policy = data.aws_iam_policy_document.runtime.json
}

resource "aws_iam_role_policy_attachment" "model" {
  role       = aws_iam_role.specialist.name
  policy_arn = var.model_policy_arn
}

resource "aws_bedrockagentcore_agent_runtime" "specialist" {
  for_each = toset(var.agents)

  agent_runtime_name = "opsmesh_${each.key}"
  description        = "Specialist runtime for ${each.key}. It does not submit purchase orders."
  role_arn           = aws_iam_role.specialist.arn

  agent_runtime_artifact {
    code_configuration {
      entry_point = ["main.py"]
      runtime     = "PYTHON_3_12"
      code {
        s3 {
          bucket     = var.artifact_bucket
          prefix     = aws_s3_object.package.key
          version_id = aws_s3_object.package.version_id
        }
      }
    }
  }

  environment_variables = {
    AGENT_NAME     = each.key
    AGENT_IDENTITY = "opsmesh/${each.key}"
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  tags = var.tags
}
