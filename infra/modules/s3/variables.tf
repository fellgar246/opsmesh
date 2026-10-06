variable "name_prefix" {
  description = "Prefix for the artifact bucket. The account id is appended so the name is unique."
  type        = string
}

variable "tags" {
  description = "Tags applied to the bucket."
  type        = map(string)
}
