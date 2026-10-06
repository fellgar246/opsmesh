variable "name_prefix" {
  description = "Prefix for the domain-event queue."
  type        = string
}

variable "tags" {
  description = "Tags applied to the queues."
  type        = map(string)
}
