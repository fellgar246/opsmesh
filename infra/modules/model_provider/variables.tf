variable "name_prefix" {
  description = "Prefix for the invoke policy."
  type        = string
}

variable "aws_region" {
  description = "Region of the foundation model."
  type        = string
}

variable "model_id" {
  description = "Foundation model the runtimes may call."
  type        = string
}

variable "tags" {
  description = "Tags applied to the policy."
  type        = map(string)
}
