variable "name_prefix" {
  description = "Prefix for the HTTP API."
  type        = string
}

variable "function_name" {
  description = "API function allowed to be invoked."
  type        = string
}

variable "function_invoke_arn" {
  description = "Invoke ARN of the API function."
  type        = string
}

variable "tags" {
  description = "Tags applied to the API."
  type        = map(string)
}
