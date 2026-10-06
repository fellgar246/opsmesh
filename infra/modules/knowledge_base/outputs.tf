output "knowledge_base_id" {
  description = "Identifier of the managed retrieval knowledge base."
  value       = aws_bedrockagent_knowledge_base.policies.id
}

output "documents_bucket" {
  description = "Bucket operators can sync policy documents into."
  value       = aws_s3_bucket.documents.bucket
}
