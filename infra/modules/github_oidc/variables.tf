variable "name_prefix" {
  description = "Prefix for the deploy role. Demo resources use the same prefix."
  type        = string
}

variable "github_repository" {
  description = "GitHub repository allowed to assume the deploy role, as owner/name."
  type        = string
}

variable "state_bucket_arn" {
  description = "Remote state bucket the deploy role may read and write."
  type        = string
}

variable "lock_table_arn" {
  description = "Lock table the deploy role may use."
  type        = string
}

variable "tags" {
  description = "Tags applied to the deploy role."
  type        = map(string)
}
