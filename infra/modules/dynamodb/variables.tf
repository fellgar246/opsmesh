variable "name_prefix" {
  description = "Prefix for the checkpoint table."
  type        = string
}

variable "tags" {
  description = "Tags applied to the table."
  type        = map(string)
}
