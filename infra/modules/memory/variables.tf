variable "name_prefix" {
  description = "Prefix used to build the memory name. Hyphens become underscores."
  type        = string
}

variable "tags" {
  description = "Tags applied to the memory resource."
  type        = map(string)
}
