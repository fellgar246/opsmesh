variable "name_prefix" {
  description = "Prefix for the knowledge base and its document bucket."
  type        = string
}

variable "aws_region" {
  description = "Region of the embedding model."
  type        = string
}

variable "tags" {
  description = "Tags applied to the knowledge base and the document bucket."
  type        = map(string)
}
