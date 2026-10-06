variable "name_prefix" {
  description = "Prefix for alarms."
  type        = string
}

variable "environment" {
  description = "Environment segment used in log group names."
  type        = string
}

variable "api_function_name" {
  description = "API function name used by the error alarm."
  type        = string
}

variable "tags" {
  description = "Tags applied to CloudWatch resources."
  type        = map(string)
}
