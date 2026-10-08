output "state_machine_arn" {
  description = "ARN of the approval wait state machine."
  value       = aws_sfn_state_machine.approval.arn
}

output "handler_arn" {
  description = "ARN of the task function the state machine invokes."
  value       = aws_lambda_function.approval.arn
}
