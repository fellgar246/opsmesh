variable "name_prefix" {
  description = "Prefix for the shared specialist execution role."
  type        = string
}

variable "agents" {
  description = "Specialists, in conversion order."
  type        = list(string)
}

variable "artifact_bucket" {
  description = "Bucket that stores the runtime package."
  type        = string
}

variable "artifact_bucket_arn" {
  description = "ARN of the artifact bucket."
  type        = string
}

variable "model_policy_arn" {
  description = "Policy that allows the configured foundation model."
  type        = string
}

variable "log_group_arn" {
  description = "Log group the runtime may write."
  type        = string
}

variable "tags" {
  description = "Tags applied to runtimes and the execution role."
  type        = map(string)
}
