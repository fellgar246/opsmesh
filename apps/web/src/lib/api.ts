import { apiBaseUrl } from "./identity";
import type { ReorderResponse, Tenant, WorkflowRun } from "./types";

export class ApiError extends Error {
  status: number;
  fieldErrors: Record<string, string>;

  constructor(status: number, message: string, fieldErrors: Record<string, string> = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.fieldErrors = fieldErrors;
  }
}

type ErrorBody = {
  detail?: unknown;
};

const fieldLabels: Record<string, string> = {
  sku: "SKU",
  region: "Region",
  service_level: "Service level",
  requested_by: "Requested by",
  supplier_id: "Supplier",
  candidates: "Candidates",
};

export function parseApiError(status: number, body: unknown): ApiError {
  const detail = isRecord(body) ? body.detail : undefined;
  if (Array.isArray(detail)) {
    const fieldErrors: Record<string, string> = {};
    const messages: string[] = [];
    for (const item of detail) {
      if (!isRecord(item)) continue;
      const loc = Array.isArray(item.loc) ? item.loc.filter((part) => part !== "body") : [];
      const field = loc.length > 0 ? String(loc[loc.length - 1]) : "";
      const raw = typeof item.msg === "string" ? item.msg : "Invalid value";
      const label = fieldLabels[field] ?? field;
      const message = field ? `${label}: ${raw}` : raw;
      if (field) fieldErrors[field] = message;
      messages.push(message);
    }
    return new ApiError(status, messages.join(" ") || "The request was rejected.", fieldErrors);
  }
  if (typeof detail === "string" && detail.length > 0) {
    return new ApiError(status, detail);
  }
  if (status === 404) return new ApiError(status, "Not found");
  return new ApiError(status, "The request failed.");
}

function isRecord(value: unknown): value is Record<string, unknown> & ErrorBody {
  return typeof value === "object" && value !== null;
}

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      ...init,
      headers: {
        accept: "application/json",
        ...(init?.body ? { "content-type": "application/json" } : {}),
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError(0, "Couldn't reach the API. Retrying…");
  }
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    throw parseApiError(response.status, body);
  }
  return (await response.json()) as T;
}

export function tenantPath(tenantId: string, path: string): string {
  return `/tenants/${encodeURIComponent(tenantId)}${path}`;
}

export function listTenants(): Promise<{ tenants: Tenant[] }> {
  return apiRequest("/tenants");
}

export function listWorkflows(tenantId: string): Promise<{ workflow_runs: WorkflowRun[] }> {
  return apiRequest(tenantPath(tenantId, "/workflow-runs"));
}

export function getWorkflow(tenantId: string, runId: string): Promise<WorkflowRun> {
  return apiRequest(tenantPath(tenantId, `/workflow-runs/${runId}`));
}

export function startReorder(
  tenantId: string,
  body: { requested_by: string; sku: string; region: string; service_level: number },
): Promise<ReorderResponse> {
  return apiRequest(tenantPath(tenantId, "/operations/reorder"), {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function startSupplierDelay(
  tenantId: string,
  body: { requested_by: string; supplier_id: string },
): Promise<{ run_id: string }> {
  return apiRequest(tenantPath(tenantId, "/operations/supplier-delays"), {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function startBudgetPlan(
  tenantId: string,
  body: {
    requested_by: string;
    candidates: { sku: string; region: string; service_level: number }[];
  },
): Promise<{ run_id: string }> {
  return apiRequest(tenantPath(tenantId, "/operations/budget-plans"), {
    method: "POST",
    body: JSON.stringify(body),
  });
}
