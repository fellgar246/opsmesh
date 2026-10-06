variable "aws_region" {
  description = "Region for the state bucket, lock table, and deploy role."
  type        = string
  default     = "us-east-1"
}

variable "state_bucket_name" {
  description = "Globally unique name for the encrypted remote state bucket."
  type        = string
}

variable "lock_table_name" {
  description = "DynamoDB table that locks remote state."
  type        = string
  default     = "opsmesh-dev-locks"
}

variable "github_repository" {
  description = "GitHub repository allowed to assume the deploy role, as owner/name."
  type        = string
}

variable "name_prefix" {
  description = "Prefix shared with the demo stack."
  type        = string
  default     = "opsmesh-dev"
}

variable "environment" {
  description = "Environment tag value."
  type        = string
  default     = "dev"
}
