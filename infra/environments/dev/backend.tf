terraform {
  backend "s3" {
    key     = "opsmesh/dev/terraform.tfstate"
    encrypt = true
  }
}
