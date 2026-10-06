resource "aws_bedrockagentcore_memory" "demo" {
  name                  = "${replace(var.name_prefix, "-", "_")}_memory"
  description           = "Optional long-term memory. Off unless showcase is enabled."
  event_expiry_duration = 30
  tags                  = var.tags
}
