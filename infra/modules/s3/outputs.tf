output "bucket_name" {
  description = "Artifact bucket that holds runtime packages."
  value       = aws_s3_bucket.artifacts.bucket
}

output "bucket_arn" {
  description = "ARN of the artifact bucket."
  value       = aws_s3_bucket.artifacts.arn
}
