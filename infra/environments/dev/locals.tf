locals {
  name_prefix = "opsmesh-${var.environment}"
  tags = {
    Project     = "opsmesh"
    Environment = var.environment
    ManagedBy   = "terraform"
    CostCenter  = "learning"
    AutoCleanup = "true"
  }
}
