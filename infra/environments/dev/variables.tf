variable "aws_region" {
  description = "Region for the demo stack. Override with TF_VAR_aws_region."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment tag value."
  type        = string
  default     = "dev"

  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.environment))
    error_message = "environment must be a short lowercase name."
  }
}

variable "model_id" {
  description = "Foundation model the runtimes may call."
  type        = string
  default     = "anthropic.claude-3-haiku-20240307-v1:0"
}

variable "gateway_protocol_version" {
  description = "MCP protocol version advertised by the tool gateway."
  type        = string
  default     = "2026-07-28"
}

variable "specialist_agents" {
  description = "Specialists created when agent runtimes are enabled, in conversion order."
  type        = list(string)
  default     = ["policy", "inventory", "supplier", "finance", "demand"]
}

variable "enable_agent_runtimes" {
  description = "Create specialist runtimes."
  type        = bool
  default     = true
}

variable "enable_supervisor_runtime" {
  description = "Create the supervisor runtime."
  type        = bool
  default     = true
}

variable "enable_gateway" {
  description = "Create the tool gateway."
  type        = bool
  default     = true
}

variable "enable_memory" {
  description = "Create long-term agent memory. Off unless showcase is enabled."
  type        = bool
  default     = false
}

variable "enable_managed_retrieval" {
  description = "Create the managed knowledge base. Off unless showcase is enabled."
  type        = bool
  default     = false
}

variable "enable_managed_policy" {
  description = "Create the managed policy engine. Off unless showcase is enabled."
  type        = bool
  default     = false
}

variable "enable_showcase" {
  description = "Create the daily schedule and other showcase-only resources."
  type        = bool
  default     = false
}
