variable "name_prefix" {
  description = "Prefix for the gateway and its execution role."
  type        = string
}

variable "protocol_version" {
  description = "MCP protocol version advertised by the gateway."
  type        = string
}

variable "policy_engine_arn" {
  description = "Optional managed policy engine. Null leaves the gateway without one."
  type        = string
  default     = null
}

variable "tags" {
  description = "Tags applied to the gateway and its role."
  type        = map(string)
}
