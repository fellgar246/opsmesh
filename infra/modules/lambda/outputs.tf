output "api_function_name" {
  description = "Name of the API function."
  value       = aws_lambda_function.api.function_name
}

output "api_function_arn" {
  description = "ARN of the API function."
  value       = aws_lambda_function.api.arn
}

output "api_invoke_arn" {
  description = "Invoke ARN used by the HTTP API."
  value       = aws_lambda_function.api.invoke_arn
}

output "consumer_function_name" {
  description = "Name of the consumer function."
  value       = aws_lambda_function.consumer.function_name
}
