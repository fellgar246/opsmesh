output "memory_id" {
  description = "Identifier of the memory resource."
  value       = aws_bedrockagentcore_memory.demo.id
}

output "memory_arn" {
  description = "ARN of the memory resource."
  value       = aws_bedrockagentcore_memory.demo.arn
}
