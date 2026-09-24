import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OperationsConsole } from "@/components/operations-console";
import { ApprovalList, ProposalList, WorkflowList } from "@/components/lists";
import { CostPage } from "@/components/lists";
import { TaskGraph } from "@/components/task-graph";
import { WorkflowDetail } from "@/components/workflow-detail";
import { SessionProvider } from "@/lib/session";
import type { AgentTask, WorkflowRun } from "@/lib/types";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  usePathname: () => "/workflows",
}));

const runId = "11111111-1111-4111-8111-111111111111";

function goldenRun(): WorkflowRun {
  return {
    id: runId,
    tenant_id: "northwind",
    workflow_type: "reorder",
    requested_by: "analyst@northwind.example",
    status: "WAITING_APPROVAL",
    input: { sku: "ABC-42", region: "mx-central", service_level: 0.9 },
    started_at: "2026-09-23T18:00:00Z",
    finished_at: "2026-09-23T18:00:01Z",
    cost_summary: { model_calls: 0, tool_calls: 6, latency_ms: 40, estimated_cost_cents: 0 },
    tasks: ["inventory", "demand", "supplier", "finance", "policy"].map((agent, index) => ({
      id: `22222222-2222-4222-8222-22222222222${index}`,
      workflow_run_id: runId,
      agent_type: agent,
      parent_task_id: null,
      depends_on: agent === "policy" ? ["22222222-2222-4222-8222-222222222220"] : [],
      status: "COMPLETED",
      input_hash: "abc",
      started_at: "2026-09-23T18:00:00Z",
      finished_at: "2026-09-23T18:00:01Z",
      results: [
        {
          id: `33333333-3333-4333-8333-33333333333${index}`,
          task_id: `22222222-2222-4222-8222-22222222222${index}`,
          schema_version: "1",
          facts:
            agent === "inventory"
              ? { available_units: 128, reserved_units: 12, observed_at: "2026-09-23T18:00:00Z" }
              : {
                  records: [
                    agent === "policy"
                      ? { reason: null, quantity: 250 }
                      : { observed_at: "2026-09-23T18:00:00Z" },
                  ],
                },
          evidence: [
            {
              type: "snapshot",
              source: agent,
              source_record_id: "ABC-42:mx-central",
              observed_at: "2026-09-23T18:00:00Z",
            },
          ],
          warnings: [],
          confidence: "high",
          model_id: null,
          prompt_version: null,
        },
      ],
      tool_executions: [],
    })),
    evidence: [
      {
        id: "44444444-4444-4444-8444-444444444444",
        workflow_run_id: runId,
        type: "inventory_snapshot",
        source: "inventory",
        source_record_id: "ABC-42:mx-central",
        observed_at: "2026-09-23T18:00:00Z",
        checksum: "abc123",
      },
    ],
    proposals: [
      {
        id: "55555555-5555-4555-8555-555555555555",
        workflow_run_id: runId,
        proposal_type: "reorder",
        arguments: { quantity: 250, extended_cost_cents: 925000, sku: "ABC-42" },
        estimated_impact: { extended_cost_cents: 925000 },
        risk_level: "medium",
        status: "PENDING_APPROVAL",
        created_at: "2026-09-23T18:00:01Z",
        approvals: [
          {
            id: "66666666-6666-4666-8666-666666666666",
            proposal_id: "55555555-5555-4555-8555-555555555555",
            arguments_hash: "a1b2c3d4e5f67890",
            required_role: "procurement_manager",
            status: "PENDING",
            expires_at: "2026-09-30T18:00:01Z",
            approved_by: null,
          },
        ],
      },
    ],
  };
}

function renderConsole(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SessionProvider>{ui}</SessionProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  push.mockReset();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/tenants")) {
        return json({ tenants: [{ tenant_id: "northwind", name: "Northwind" }] });
      }
      if (url.endsWith("/workflow-runs") && (!init || init.method === undefined)) {
        return json({ workflow_runs: [] });
      }
      if (url.endsWith("/operations/reorder") && init?.method === "POST") {
        return json({
          run_id: runId,
          status: "WAITING_APPROVAL",
          quantity: 250,
          requires_manager_approval: true,
          requires_finance_approval: false,
        });
      }
      if (url.endsWith(`/workflow-runs/${runId}`)) {
        return json(goldenRun());
      }
      return json({ detail: "Not found" }, 404);
    }),
  );
});

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("operations console", () => {
  it("starts the golden reorder and shows quantity and manager approval", async () => {
    const user = userEvent.setup();
    renderConsole(<OperationsConsole />);
    await user.type(await screen.findByLabelText("SKU"), "ABC-42");
    await user.type(screen.getByLabelText("Region"), "mx-central");
    await user.type(screen.getByLabelText("Service level"), "0.90");
    await user.click(screen.getByRole("button", { name: "Run workflow" }));
    expect(push).toHaveBeenCalledWith(`/workflows/${runId}`);

    renderConsole(<WorkflowDetail runId={runId} />);
    expect(await screen.findByTestId("proposal-quantity")).toHaveTextContent("250 units");
    expect(screen.getByTestId("approval-role")).toHaveTextContent("Manager approval required");
    expect(screen.getAllByTestId("status-badge")[0]).toHaveTextContent("Waiting approval");
    expect(screen.getByText("Available units")).toBeInTheDocument();
    expect(screen.getByText("128 units")).toBeInTheDocument();
  });

  it("shows an empty state when the tenant has no workflows", async () => {
    renderConsole(<WorkflowList />);
    expect(await screen.findByTestId("empty-state")).toHaveTextContent("No workflows yet");
    renderConsole(<ProposalList />);
    expect(await screen.findByText("No proposals yet")).toBeInTheDocument();
    renderConsole(<ApprovalList />);
    expect(await screen.findByText("No approvals yet")).toBeInTheDocument();
  });

  it("shows a readable error when the workflow list fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/tenants")) {
          return json({ tenants: [{ tenant_id: "northwind", name: "Northwind" }] });
        }
        return json({ detail: "tenant does not exist" }, 422);
      }),
    );
    renderConsole(<WorkflowList />);
    expect(await screen.findByTestId("error-state")).toHaveTextContent("tenant does not exist");
  });

  it("shows not found for a malformed workflow id", async () => {
    renderConsole(<WorkflowDetail runId="not-a-workflow" />);
    expect(await screen.findByTestId("not-found")).toHaveTextContent("Workflow not found");
    expect(await screen.findByTestId("not-found")).toHaveTextContent("Northwind");
  });

  it("renders cost zeros when no model calls are recorded", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/tenants")) {
          return json({ tenants: [{ tenant_id: "northwind", name: "Northwind" }] });
        }
        return json({ workflow_runs: [goldenRun()] });
      }),
    );
    renderConsole(<CostPage />);
    expect(await screen.findByTestId("cost-note")).toHaveTextContent("No model calls recorded");
    expect(screen.getByText("0 / 15")).toBeInTheDocument();
    expect(screen.getAllByText("$0.00").length).toBeGreaterThan(0);
  });

  it("shows a failed task reason without leaving the page", async () => {
    const user = userEvent.setup();
    const task = {
      id: "77777777-7777-4777-8777-777777777777",
      workflow_run_id: runId,
      agent_type: "supplier",
      parent_task_id: null,
      depends_on: [],
      status: "FAILED",
      input_hash: "abc",
      started_at: null,
      finished_at: null,
      results: [
        {
          id: "88888888-8888-4888-8888-888888888888",
          task_id: "77777777-7777-4777-8777-777777777777",
          schema_version: "1",
          facts: {},
          evidence: [],
          warnings: [{ code: "TOOL_TIMEOUT", message: "supplier.get_terms timed out" }],
          confidence: "low",
          model_id: null,
          prompt_version: null,
        },
      ],
      tool_executions: [],
    } satisfies AgentTask;
    render(<TaskGraph tasks={[task]} />);
    await user.click(screen.getByTestId("task-node-supplier"));
    expect(screen.getByTestId("task-failure-reason")).toHaveTextContent("supplier.get_terms timed out");
  });
});
