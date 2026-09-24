/** Local development identity until tenant claims come from authentication. */

export const localIdentity = {
  requestedBy: "analyst@northwind.example",
  role: "operations_analyst",
  displayName: "Operations analyst",
  roleLabel: "Operations analyst",
} as const;

export const workflowCeilings = {
  workflowsPerDay: 15,
  tasks: 12,
  modelCalls: 20,
  toolCalls: 30,
  durationSeconds: 180,
} as const;

export const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
