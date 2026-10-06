variable "name_prefix" {
  description = "Prefix for the event bus."
  type        = string
}

variable "queue_arn" {
  description = "Queue that receives domain events."
  type        = string
}

variable "queue_url" {
  description = "URL of the queue that receives domain events."
  type        = string
}

variable "enable_schedule" {
  description = "Create the daily schedule. Off unless showcase is enabled."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags applied to the bus and rules."
  type        = map(string)
}
