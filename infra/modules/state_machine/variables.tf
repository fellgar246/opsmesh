variable "name_prefix" {
  description = "Prefix for the approval wait state machine and its task function."
  type        = string
}

variable "tags" {
  description = "Tags applied to the state machine and its task function."
  type        = map(string)
}
