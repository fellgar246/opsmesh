provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = "opsmesh"
      Environment = "dev"
      ManagedBy   = "terraform"
      CostCenter  = "learning"
      AutoCleanup = "true"
    }
  }
}

module "budget" {
  source = "../../modules/budget"
}
