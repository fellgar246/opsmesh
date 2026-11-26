export const workflowStatusLabels: Record<string, { label: string; tone: string }> = {
  PENDING: { label: "Pending", tone: "neutral" },
  RUNNING: { label: "Running", tone: "info" },
  WAITING_APPROVAL: { label: "Waiting approval", tone: "warning" },
  COMPLETED: { label: "Completed", tone: "success" },
  FAILED: { label: "Failed", tone: "danger" },
  CANCELLED: { label: "Cancelled", tone: "neutral" },
};

export const taskStatusLabels: Record<string, { label: string; tone: string }> = {
  PENDING: { label: "Pending", tone: "neutral" },
  READY: { label: "Ready", tone: "info" },
  RUNNING: { label: "Running", tone: "info" },
  COMPLETED: { label: "Completed", tone: "success" },
  FAILED: { label: "Failed", tone: "danger" },
  CANCELLED: { label: "Cancelled", tone: "neutral" },
  SKIPPED: { label: "Skipped", tone: "neutral" },
};

export const proposalStatusLabels: Record<string, { label: string; tone: string }> = {
  DRAFT: { label: "Draft", tone: "neutral" },
  PENDING_APPROVAL: { label: "Pending approval", tone: "warning" },
  APPROVED: { label: "Approved", tone: "success" },
  REJECTED: { label: "Rejected", tone: "danger" },
  EXPIRED: { label: "Expired", tone: "expired" },
  SUPERSEDED: { label: "Superseded", tone: "neutral" },
};

export const approvalStatusLabels: Record<string, { label: string; tone: string }> = {
  PENDING: { label: "Pending", tone: "warning" },
  APPROVED: { label: "Approved", tone: "success" },
  REJECTED: { label: "Rejected", tone: "danger" },
  EXPIRED: { label: "Expired", tone: "expired" },
};

export const reasonLabels: Record<string, { label: string; tone: string; detail: string }> = {
  feasible: { label: "Feasible", tone: "success", detail: "" },
  adequate_stock: {
    label: "No reorder needed",
    tone: "neutral",
    detail: "Available stock already covers selected demand.",
  },
  insufficient_budget: {
    label: "Insufficient budget",
    tone: "danger",
    detail: "Budget cannot fund the supplier minimum order.",
  },
  supplier_not_approved: {
    label: "Supplier blocked",
    tone: "danger",
    detail: "Supplier status is not approved.",
  },
  missing_evidence: {
    label: "Missing evidence",
    tone: "danger",
    detail: "A required fact was not available. No numbers were assumed.",
  },
  workflow_timeout: {
    label: "Timed out",
    tone: "danger",
    detail: "The workflow exceeded 180 seconds.",
  },
};

export const roleLabels: Record<string, string> = {
  viewer: "Viewer",
  operations_analyst: "Operations analyst",
  procurement_manager: "Manager",
  finance_approver: "Finance approver",
  admin: "Admin",
};

export const agentLabels: Record<string, string> = {
  inventory: "Inventory",
  demand: "Demand",
  supplier: "Supplier",
  finance: "Finance",
  policy: "Policy",
  supervisor: "Supervisor",
  procurement: "Procurement",
  rules: "Deterministic engine",
};

export const workflowTypeLabels: Record<string, string> = {
  reorder: "Reorder",
  supplier_delay: "Supplier delay",
  budget_plan: "Budget plan",
};

export function labelFor(map: Record<string, { label: string }>, value: string): string {
  return map[value]?.label ?? value;
}
