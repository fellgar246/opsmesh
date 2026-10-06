variable "name_prefix" {
  description = "Prefix used to build the policy engine name. Hyphens become underscores."
  type        = string
}

variable "tags" {
  description = "Tags applied to the policy engine."
  type        = map(string)
}
