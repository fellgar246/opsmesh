export type CostSummary = {
  model_calls: number;
  tool_calls: number;
  latency_ms: number;
  estimated_cost_cents: number | null;
};

export type EvidenceItem = {
  type: string;
  source: string;
  source_record_id: string;
  observed_at: string;
};

export type WarningItem = {
  code: string;
  message: string;
};

export type AgentResult = {
  id: string;
  task_id: string;
  schema_version: string;
  facts: Record<string, unknown>;
  evidence: EvidenceItem[];
  warnings: WarningItem[];
  confidence: string;
  model_id: string | null;
  prompt_version: string | null;
};

export type ToolExecution = {
  id: string;
  task_id: string;
  tool_name: string;
  arguments_hash: string;
  status: string;
  latency_ms: number;
  retry_count: number;
  result_reference: string | null;
};

export type AgentTask = {
  id: string;
  workflow_run_id: string;
  agent_type: string;
  parent_task_id: string | null;
  depends_on: string[];
  status: string;
  input_hash: string;
  started_at: string | null;
  finished_at: string | null;
  results: AgentResult[];
  tool_executions: ToolExecution[];
};

export type Evidence = {
  id: string;
  workflow_run_id: string;
  type: string;
  source: string;
  source_record_id: string;
  observed_at: string;
  checksum: string;
};

export type Approval = {
  id: string;
  proposal_id: string;
  arguments_hash: string;
  required_role: string;
  status: string;
  expires_at: string;
  approved_by: string | null;
};

export type Proposal = {
  id: string;
  workflow_run_id: string;
  proposal_type: string;
  arguments: Record<string, unknown>;
  estimated_impact: Record<string, unknown>;
  risk_level: string;
  status: string;
  created_at: string;
  approvals: Approval[];
};

export type WorkflowRun = {
  id: string;
  tenant_id: string;
  workflow_type: string;
  requested_by: string;
  status: string;
  input: Record<string, unknown>;
  started_at: string;
  finished_at: string | null;
  cost_summary: CostSummary;
  tasks: AgentTask[];
  evidence: Evidence[];
  proposals: Proposal[];
};

export type Tenant = {
  tenant_id: string;
  name: string;
};

export type ReorderResponse = {
  run_id: string;
  status: string;
  quantity: number | null;
  requires_manager_approval: boolean;
  requires_finance_approval: boolean;
};

export const terminalStatuses = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
