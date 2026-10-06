resource "aws_cognito_user_pool" "demo" {
  name = "${var.name_prefix}-users"

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_numbers   = true
    require_symbols   = true
    require_uppercase = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "admin_only"
      priority = 1
    }
  }

  tags = var.tags
}

resource "aws_cognito_user_pool_client" "console" {
  name         = "${var.name_prefix}-console"
  user_pool_id = aws_cognito_user_pool.demo.id

  generate_secret = false

  explicit_auth_flows = [
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_USER_SRP_AUTH",
  ]

  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 30

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }
}

resource "aws_cognito_user_group" "roles" {
  for_each = toset(var.roles)

  name         = each.value
  user_pool_id = aws_cognito_user_pool.demo.id
  description  = "Control-plane role ${each.value}."
}
