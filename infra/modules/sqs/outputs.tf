output "queue_arn" {
  description = "ARN of the domain-event queue."
  value       = aws_sqs_queue.events.arn
}

output "queue_url" {
  description = "URL of the domain-event queue."
  value       = aws_sqs_queue.events.url
}

output "queue_name" {
  description = "Name of the domain-event queue."
  value       = aws_sqs_queue.events.name
}
