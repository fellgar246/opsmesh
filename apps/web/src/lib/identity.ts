/** Local development identity until tenant claims come from authentication. */

export const localIdentities = {
  viewer: { actor: "viewer@northwind.example", label: "Viewer" },
  operations_analyst: { actor: "analyst@northwind.example", label: "Operations analyst" },
  procurement_manager: { actor: "manager@northwind.example", label: "Procurement manager" },
  finance_approver: { actor: "finance@northwind.example", label: "Finance approver" },
  admin: { actor: "admin@northwind.example", label: "Admin" },
} as const;

export type LocalRole = keyof typeof localIdentities;

export function isLocalRole(value: string): value is LocalRole {
  return Object.prototype.hasOwnProperty.call(localIdentities, value);
}

export const localIdentity = {
  requestedBy: localIdentities.operations_analyst.actor,
  role: "operations_analyst" satisfies LocalRole,
  displayName: localIdentities.operations_analyst.label,
  roleLabel: localIdentities.operations_analyst.label,
} as const;

export const workflowCeilings = {
  workflowsPerDay: 15,
  tasks: 12,
  modelCalls: 20,
  toolCalls: 30,
  durationSeconds: 180,
  supervisorIterations: 4,
} as const;

export const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
