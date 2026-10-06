variable "name_prefix" {
  description = "Prefix for the user pool."
  type        = string
}

variable "tags" {
  description = "Tags applied to the user pool."
  type        = map(string)
}

variable "roles" {
  description = "Group names that match control-plane role identifiers."
  type        = list(string)
  default = [
    "viewer",
    "operations_analyst",
    "procurement_manager",
    "finance_approver",
    "admin",
  ]
}
