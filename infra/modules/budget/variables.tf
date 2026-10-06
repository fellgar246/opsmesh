variable "name_prefix" {
  description = "Prefix for the budget and the alert topic."
  type        = string
  default     = "opsmesh-dev"
}

variable "environment" {
  description = "Environment tag value used by the budget cost filter."
  type        = string
  default     = "dev"
}

variable "tags" {
  description = "Tags applied to budget resources."
  type        = map(string)
  default = {
    Project     = "opsmesh"
    Environment = "dev"
    ManagedBy   = "terraform"
    CostCenter  = "learning"
    AutoCleanup = "true"
  }
}
