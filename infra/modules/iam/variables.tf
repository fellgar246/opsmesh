variable "name_prefix" {
  description = "Prefix for execution roles."
  type        = string
}

variable "queue_arn" {
  description = "Domain-event queue the consumer reads."
  type        = string
}

variable "checkpoint_table_arn" {
  description = "Checkpoint table the consumer writes."
  type        = string
}

variable "api_log_group_arn" {
  description = "Log group for the API function."
  type        = string
}

variable "consumer_log_group_arn" {
  description = "Log group for the consumer function."
  type        = string
}

variable "tags" {
  description = "Tags applied to roles."
  type        = map(string)
}
