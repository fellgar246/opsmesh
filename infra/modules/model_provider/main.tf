data "aws_iam_policy_document" "invoke" {
  statement {
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock:Converse",
      "bedrock:ConverseStream",
    ]
    resources = [
      "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.model_id}",
    ]
  }
}

resource "aws_iam_policy" "invoke" {
  name   = "${var.name_prefix}-invoke-model"
  policy = data.aws_iam_policy_document.invoke.json
  tags   = var.tags
}
