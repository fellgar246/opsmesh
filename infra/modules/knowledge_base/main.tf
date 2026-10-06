data "aws_caller_identity" "current" {}

locals {
  documents_bucket = "${var.name_prefix}-knowledge-${data.aws_caller_identity.current.account_id}"
  vector_bucket    = "${var.name_prefix}-vectors-${data.aws_caller_identity.current.account_id}"
  embedding_model  = "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.titan-embed-text-v2:0"
}

resource "aws_s3_bucket" "documents" {
  bucket = local.documents_bucket
  tags   = var.tags
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket                  = aws_s3_bucket.documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3vectors_vector_bucket" "policies" {
  vector_bucket_name = local.vector_bucket
}

resource "aws_s3vectors_index" "policies" {
  index_name         = "policies"
  vector_bucket_name = aws_s3vectors_vector_bucket.policies.vector_bucket_name
  data_type          = "float32"
  dimension          = 256
  distance_metric    = "euclidean"
}

data "aws_iam_policy_document" "assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "knowledge" {
  name               = "${var.name_prefix}-knowledge"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "knowledge" {
  statement {
    effect  = "Allow"
    actions = ["bedrock:InvokeModel"]
    resources = [
      local.embedding_model,
    ]
  }

  statement {
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:ListBucket",
    ]
    resources = [
      aws_s3_bucket.documents.arn,
      "${aws_s3_bucket.documents.arn}/*",
    ]
  }

  statement {
    effect = "Allow"
    actions = [
      "s3vectors:GetIndex",
      "s3vectors:QueryVectors",
      "s3vectors:PutVectors",
      "s3vectors:GetVectors",
      "s3vectors:DeleteVectors",
    ]
    resources = [aws_s3vectors_index.policies.index_arn]
  }
}

resource "aws_iam_role_policy" "knowledge" {
  name   = "retrieve"
  role   = aws_iam_role.knowledge.id
  policy = data.aws_iam_policy_document.knowledge.json
}

resource "aws_bedrockagent_knowledge_base" "policies" {
  name     = "${var.name_prefix}-policies"
  role_arn = aws_iam_role.knowledge.arn

  knowledge_base_configuration {
    type = "VECTOR"

    vector_knowledge_base_configuration {
      embedding_model_arn = local.embedding_model

      embedding_model_configuration {
        bedrock_embedding_model_configuration {
          dimensions          = 256
          embedding_data_type = "FLOAT32"
        }
      }
    }
  }

  storage_configuration {
    type = "S3_VECTORS"

    s3_vectors_configuration {
      index_arn = aws_s3vectors_index.policies.index_arn
    }
  }

  tags = var.tags
}

resource "aws_bedrockagent_data_source" "policies" {
  knowledge_base_id = aws_bedrockagent_knowledge_base.policies.id
  name              = "policies"

  data_source_configuration {
    type = "S3"

    s3_configuration {
      bucket_arn = aws_s3_bucket.documents.arn
    }
  }
}
