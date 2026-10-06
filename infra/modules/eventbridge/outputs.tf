output "bus_name" {
  description = "Custom bus that receives domain events."
  value       = aws_cloudwatch_event_bus.domain.name
}

output "bus_arn" {
  description = "ARN of the domain event bus."
  value       = aws_cloudwatch_event_bus.domain.arn
}
