resource "aws_bedrockagentcore_policy_engine" "demo" {
  name        = "${replace(var.name_prefix, "-", "_")}_policy"
  description = "Second vote for agent and tool calls. The local policy engine remains the source of truth."
  tags        = var.tags
}

resource "aws_bedrockagentcore_policy" "read" {
  name             = "opsmesh_read"
  description      = "Permit a read action. This statement does not authorize a purchase."
  policy_engine_id = aws_bedrockagentcore_policy_engine.demo.policy_engine_id

  definition {
    cedar {
      statement = "permit(principal, action == Action::\"Read\", resource);"
    }
  }
}
