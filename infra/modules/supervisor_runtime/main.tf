data "archive_file" "package" {
  type        = "zip"
  source_file = "${path.module}/../../functions/supervisor/main.py"
  output_path = "${path.module}/.build/supervisor.zip"
}

resource "aws_s3_object" "package" {
  bucket      = var.artifact_bucket
  key         = "agents/supervisor.zip"
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

resource "aws_iam_role" "supervisor" {
  name               = "${var.name_prefix}-supervisor"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "runtime" {
  statement {
    effect = "Allow"
    actions = [
      "s3:GetObject",
    ]
    resources = ["${var.artifact_bucket_arn}/agents/supervisor.zip"]
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
  role   = aws_iam_role.supervisor.id
  policy = data.aws_iam_policy_document.runtime.json
}

resource "aws_iam_role_policy_attachment" "model" {
  role       = aws_iam_role.supervisor.name
  policy_arn = var.model_policy_arn
}

resource "aws_bedrockagentcore_agent_runtime" "supervisor" {
  agent_runtime_name = "opsmesh_supervisor"
  description        = "Supervisor runtime. It does not query the simulator database."
  role_arn           = aws_iam_role.supervisor.arn

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
    AGENT_NAME     = "supervisor"
    AGENT_IDENTITY = "opsmesh/supervisor"
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  tags = var.tags
}
