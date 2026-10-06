variable "name_prefix" {
  description = "Prefix for function names."
  type        = string
}

variable "api_role_arn" {
  description = "Execution role for the API function."
  type        = string
}

variable "consumer_role_arn" {
  description = "Execution role for the consumer function."
  type        = string
}

variable "api_log_group" {
  description = "Log group name for the API function."
  type        = string
}

variable "consumer_log_group" {
  description = "Log group name for the consumer function."
  type        = string
}

variable "queue_arn" {
  description = "Queue that invokes the consumer."
  type        = string
}

variable "checkpoint_table_name" {
  description = "Checkpoint table name passed to the consumer."
  type        = string
}

variable "tags" {
  description = "Tags applied to functions."
  type        = map(string)
}
