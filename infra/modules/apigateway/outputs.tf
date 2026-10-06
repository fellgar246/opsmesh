output "api_endpoint" {
  description = "Base URL of the HTTP API."
  value       = aws_apigatewayv2_api.http.api_endpoint
}

output "api_id" {
  description = "Identifier of the HTTP API."
  value       = aws_apigatewayv2_api.http.id
}
