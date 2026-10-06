data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  account = data.aws_caller_identity.current.account_id
  region  = data.aws_region.current.region
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
  tags = var.tags

  lifecycle {
    ignore_changes = [thumbprint_list]
  }
}

data "aws_iam_policy_document" "assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:environment:demo"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name               = "${var.name_prefix}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "deploy" {
  statement {
    sid    = "StateAndArtifacts"
    effect = "Allow"
    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:ListBucket",
      "s3:GetBucketLocation",
      "s3:GetBucketVersioning",
      "s3:PutBucketVersioning",
      "s3:GetEncryptionConfiguration",
      "s3:PutEncryptionConfiguration",
      "s3:GetBucketPublicAccessBlock",
      "s3:PutBucketPublicAccessBlock",
      "s3:GetBucketTagging",
      "s3:PutBucketTagging",
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:GetObjectVersion",
      "s3:DeleteObjectVersion",
      "s3:PutObjectTagging",
    ]
    resources = [
      var.state_bucket_arn,
      "${var.state_bucket_arn}/*",
      "arn:aws:s3:::${var.name_prefix}-*",
      "arn:aws:s3:::${var.name_prefix}-*/*",
    ]
  }

  statement {
    sid    = "Tables"
    effect = "Allow"
    actions = [
      "dynamodb:CreateTable",
      "dynamodb:DeleteTable",
      "dynamodb:DescribeTable",
      "dynamodb:UpdateTable",
      "dynamodb:TagResource",
      "dynamodb:UntagResource",
      "dynamodb:ListTagsOfResource",
      "dynamodb:DescribeTimeToLive",
      "dynamodb:UpdateTimeToLive",
      "dynamodb:DescribeContinuousBackups",
      "dynamodb:UpdateContinuousBackups",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
    ]
    resources = [
      var.lock_table_arn,
      "arn:aws:dynamodb:${local.region}:${local.account}:table/${var.name_prefix}-*",
    ]
  }

  statement {
    sid    = "Functions"
    effect = "Allow"
    actions = [
      "lambda:CreateFunction",
      "lambda:DeleteFunction",
      "lambda:GetFunction",
      "lambda:GetFunctionConfiguration",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
      "lambda:AddPermission",
      "lambda:RemovePermission",
      "lambda:GetPolicy",
      "lambda:TagResource",
      "lambda:UntagResource",
      "lambda:ListTags",
      "lambda:CreateEventSourceMapping",
      "lambda:DeleteEventSourceMapping",
      "lambda:GetEventSourceMapping",
      "lambda:UpdateEventSourceMapping",
      "lambda:ListEventSourceMappings",
    ]
    resources = [
      "arn:aws:lambda:${local.region}:${local.account}:function:${var.name_prefix}-*",
      "arn:aws:lambda:${local.region}:${local.account}:event-source-mapping:*",
    ]
  }

  statement {
    sid    = "HttpApi"
    effect = "Allow"
    actions = [
      "apigateway:GET",
      "apigateway:POST",
      "apigateway:PUT",
      "apigateway:PATCH",
      "apigateway:DELETE",
      "apigateway:TagResource",
    ]
    resources = ["arn:aws:apigateway:${local.region}::/apis", "arn:aws:apigateway:${local.region}::/apis/*", "arn:aws:apigateway:${local.region}::/tags/*"]
  }

  statement {
    sid    = "Queues"
    effect = "Allow"
    actions = [
      "sqs:CreateQueue",
      "sqs:DeleteQueue",
      "sqs:GetQueueAttributes",
      "sqs:SetQueueAttributes",
      "sqs:GetQueueUrl",
      "sqs:TagQueue",
      "sqs:UntagQueue",
      "sqs:ListQueueTags",
    ]
    resources = ["arn:aws:sqs:${local.region}:${local.account}:${var.name_prefix}-*"]
  }

  statement {
    sid    = "Events"
    effect = "Allow"
    actions = [
      "events:CreateEventBus",
      "events:DeleteEventBus",
      "events:DescribeEventBus",
      "events:PutRule",
      "events:DeleteRule",
      "events:DescribeRule",
      "events:PutTargets",
      "events:RemoveTargets",
      "events:ListTargetsByRule",
      "events:ListTagsForResource",
      "events:TagResource",
      "events:UntagResource",
    ]
    resources = [
      "arn:aws:events:${local.region}:${local.account}:event-bus/${var.name_prefix}*",
      "arn:aws:events:${local.region}:${local.account}:rule/${var.name_prefix}*",
      "arn:aws:events:${local.region}:${local.account}:rule/${var.name_prefix}*/*",
    ]
  }

  statement {
    sid    = "Logs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:DescribeLogGroups",
      "logs:PutRetentionPolicy",
      "logs:ListTagsForResource",
      "logs:TagResource",
      "logs:UntagResource",
    ]
    resources = ["arn:aws:logs:${local.region}:${local.account}:log-group:/opsmesh/*"]
  }

  statement {
    sid    = "Alarms"
    effect = "Allow"
    actions = [
      "cloudwatch:PutMetricAlarm",
      "cloudwatch:DeleteAlarms",
      "cloudwatch:DescribeAlarms",
      "cloudwatch:ListTagsForResource",
      "cloudwatch:TagResource",
      "cloudwatch:UntagResource",
    ]
    resources = ["arn:aws:cloudwatch:${local.region}:${local.account}:alarm:${var.name_prefix}-*"]
  }

  statement {
    sid    = "UserPool"
    effect = "Allow"
    actions = [
      "cognito-idp:CreateUserPool",
      "cognito-idp:DeleteUserPool",
      "cognito-idp:DescribeUserPool",
      "cognito-idp:UpdateUserPool",
      "cognito-idp:CreateUserPoolClient",
      "cognito-idp:DeleteUserPoolClient",
      "cognito-idp:DescribeUserPoolClient",
      "cognito-idp:UpdateUserPoolClient",
      "cognito-idp:CreateGroup",
      "cognito-idp:DeleteGroup",
      "cognito-idp:GetGroup",
      "cognito-idp:TagResource",
      "cognito-idp:UntagResource",
      "cognito-idp:ListTagsForResource",
    ]
    resources = ["arn:aws:cognito-idp:${local.region}:${local.account}:userpool/*"]
  }

  statement {
    sid    = "Alerts"
    effect = "Allow"
    actions = [
      "sns:CreateTopic",
      "sns:DeleteTopic",
      "sns:GetTopicAttributes",
      "sns:SetTopicAttributes",
      "sns:TagResource",
      "sns:UntagResource",
      "sns:ListTagsForResource",
    ]
    resources = ["arn:aws:sns:${local.region}:${local.account}:${var.name_prefix}-*"]
  }

  statement {
    sid    = "Budget"
    effect = "Allow"
    actions = [
      "budgets:ModifyBudget",
      "budgets:ViewBudget",
    ]
    resources = ["arn:aws:budgets::${local.account}:budget/*"]
  }

  statement {
    sid    = "Roles"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:GetRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:ListRoleTags",
      "iam:PutRolePolicy",
      "iam:GetRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:ListRolePolicies",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:PassRole",
    ]
    resources = ["arn:aws:iam::${local.account}:role/${var.name_prefix}-*"]
  }

  statement {
    sid    = "CustomerPolicies"
    effect = "Allow"
    actions = [
      "iam:CreatePolicy",
      "iam:DeletePolicy",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:ListPolicyVersions",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicyVersion",
      "iam:TagPolicy",
      "iam:UntagPolicy",
    ]
    resources = ["arn:aws:iam::${local.account}:policy/${var.name_prefix}-*"]
  }

  statement {
    sid    = "KnowledgeBase"
    effect = "Allow"
    actions = [
      "bedrock:CreateKnowledgeBase",
      "bedrock:UpdateKnowledgeBase",
      "bedrock:DeleteKnowledgeBase",
      "bedrock:GetKnowledgeBase",
      "bedrock:CreateDataSource",
      "bedrock:UpdateDataSource",
      "bedrock:DeleteDataSource",
      "bedrock:GetDataSource",
      "bedrock:TagResource",
      "bedrock:UntagResource",
      "bedrock:ListTagsForResource",
    ]
    resources = ["arn:aws:bedrock:${local.region}:${local.account}:knowledge-base/*"]
  }

  statement {
    sid    = "AgentCore"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:CreateAgentRuntime",
      "bedrock-agentcore:UpdateAgentRuntime",
      "bedrock-agentcore:DeleteAgentRuntime",
      "bedrock-agentcore:GetAgentRuntime",
      "bedrock-agentcore:CreateGateway",
      "bedrock-agentcore:UpdateGateway",
      "bedrock-agentcore:DeleteGateway",
      "bedrock-agentcore:GetGateway",
      "bedrock-agentcore:CreateMemory",
      "bedrock-agentcore:UpdateMemory",
      "bedrock-agentcore:DeleteMemory",
      "bedrock-agentcore:GetMemory",
      "bedrock-agentcore:CreatePolicyEngine",
      "bedrock-agentcore:UpdatePolicyEngine",
      "bedrock-agentcore:DeletePolicyEngine",
      "bedrock-agentcore:GetPolicyEngine",
      "bedrock-agentcore:CreatePolicy",
      "bedrock-agentcore:UpdatePolicy",
      "bedrock-agentcore:DeletePolicy",
      "bedrock-agentcore:GetPolicy",
      "bedrock-agentcore:TagResource",
      "bedrock-agentcore:UntagResource",
      "bedrock-agentcore:ListTagsForResource",
    ]
    resources = ["arn:aws:bedrock-agentcore:${local.region}:${local.account}:*"]
  }

  statement {
    sid    = "Vectors"
    effect = "Allow"
    actions = [
      "s3vectors:CreateVectorBucket",
      "s3vectors:DeleteVectorBucket",
      "s3vectors:GetVectorBucket",
      "s3vectors:CreateIndex",
      "s3vectors:DeleteIndex",
      "s3vectors:GetIndex",
      "s3vectors:TagResource",
      "s3vectors:UntagResource",
    ]
    resources = [
      "arn:aws:s3vectors:${local.region}:${local.account}:bucket/${var.name_prefix}-*",
      "arn:aws:s3vectors:${local.region}:${local.account}:bucket/${var.name_prefix}-*/*",
    ]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}
