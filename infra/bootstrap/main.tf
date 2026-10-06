provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "opsmesh"
      Environment = var.environment
      ManagedBy   = "terraform"
      CostCenter  = "learning"
      AutoCleanup = "true"
    }
  }
}

locals {
  tags = {
    Project     = "opsmesh"
    Environment = var.environment
    ManagedBy   = "terraform"
    CostCenter  = "learning"
    AutoCleanup = "true"
  }
}

resource "aws_s3_bucket" "state" {
  bucket = var.state_bucket_name
  tags   = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "lock" {
  name         = var.lock_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

module "github_oidc" {
  source = "../modules/github_oidc"

  name_prefix       = var.name_prefix
  github_repository = var.github_repository
  state_bucket_arn  = aws_s3_bucket.state.arn
  lock_table_arn    = aws_dynamodb_table.lock.arn
  tags              = local.tags
}
