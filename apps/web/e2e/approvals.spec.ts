import { expect, test, type Page, type Route } from "@playwright/test";

const approvalId = "66666666-6666-4666-8666-666666666666";
const proposalId = "55555555-5555-4555-8555-555555555555";
const runId = "11111111-1111-4111-8111-111111111111";
const liveHash = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6abcd";
const staleHash = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

function approval(overrides: Record<string, unknown> = {}) {
  return {
    id: approvalId,
    tenant_id: "northwind",
    proposal_id: proposalId,
    workflow_run_id: runId,
    arguments_hash: liveHash,
    live_arguments_hash: liveHash,
    required_role: "procurement_manager",
    status: "PENDING",
    expires_at: "2026-10-06T18:00:00Z",
    approved_by: null,
    proposal_status: "PENDING_APPROVAL",
    quantity: 250,
    sku: "ABC-42",
    supplier_id: "andina-supply",
    extended_cost_cents: 925000,
    requested_at: "2026-10-03T18:00:00Z",
    workflow_type: "reorder",
    requested_by: "analyst@northwind.example",
    proposal_approvals: [
      {
        id: approvalId,
        required_role: "procurement_manager",
        status: "PENDING",
        arguments_hash: liveHash,
        live_arguments_hash: liveHash,
        approved_by: null,
        expires_at: "2026-10-06T18:00:00Z",
      },
    ],
    ...overrides,
  };
}

function workflow() {
  return {
    id: runId,
    tenant_id: "northwind",
    workflow_type: "reorder",
    requested_by: "analyst@northwind.example",
    status: "WAITING_APPROVAL",
    input: { sku: "ABC-42", region: "mx-central", service_level: 0.9 },
    started_at: "2026-10-03T18:00:00Z",
    finished_at: "2026-10-03T18:00:01Z",
    cost_summary: {
      model_calls: 0,
      tool_calls: 6,
      latency_ms: 40,
      estimated_cost_cents: 0,
    },
    tasks: [
      {
        id: "22222222-2222-4222-8222-222222222220",
        workflow_run_id: runId,
        agent_type: "inventory",
        parent_task_id: null,
        depends_on: [],
        status: "COMPLETED",
        input_hash: "abc",
        started_at: "2026-10-03T18:00:00Z",
        finished_at: "2026-10-03T18:00:01Z",
        results: [
          {
            id: "33333333-3333-4333-8333-333333333330",
            task_id: "22222222-2222-4222-8222-222222222220",
            schema_version: "1",
            facts: { available_units: 128 },
            evidence: [
              {
                type: "snapshot",
                source: "inventory",
                source_record_id: "ABC-42:mx-central",
                observed_at: "2026-10-03T18:00:00Z",
              },
            ],
            warnings: [],
            confidence: "high",
            model_id: null,
            prompt_version: null,
          },
        ],
        tool_executions: [],
      },
    ],
    evidence: [
      {
        id: "44444444-4444-4444-8444-444444444444",
        workflow_run_id: runId,
        type: "inventory_snapshot",
        source: "inventory",
        source_record_id: "ABC-42:mx-central",
        observed_at: "2026-10-03T18:00:00Z",
        checksum: "abc123",
      },
    ],
    proposals: [
      {
        id: proposalId,
        workflow_run_id: runId,
        proposal_type: "reorder",
        arguments: {
          quantity: 250,
          sku: "ABC-42",
          extended_cost_cents: 925000,
          explanation: "The purchase quantity is 250. The purchase total requires manager approval.",
        },
        estimated_impact: { extended_cost_cents: 925000 },
        risk_level: "medium",
        status: "PENDING_APPROVAL",
        created_at: "2026-10-03T18:00:01Z",
        approvals: [
          {
            id: approvalId,
            proposal_id: proposalId,
            arguments_hash: liveHash,
            required_role: "procurement_manager",
            status: "PENDING",
            expires_at: "2026-10-06T18:00:00Z",
            approved_by: null,
          },
        ],
      },
    ],
  };
}

const api = "http://localhost:8000/tenants/northwind";

async function installRoutes(page: Page, current = approval()) {
  await page.route("http://localhost:8000/auth/session", async (route) => {
    const body = route.request().postDataJSON() as { tenant_id?: string; role?: string };
    const role = body.role ?? "operations_analyst";
    const tenantId = body.tenant_id ?? "northwind";
    await route.fulfill({
      json: {
        access_token: `token.${role}`,
        token_type: "Bearer",
        user_id: `${role}@${tenantId}.example`,
        tenant_id: tenantId,
        roles: [role],
      },
    });
  });
  await page.route("http://localhost:8000/tenants", async (route) => {
    await route.fulfill({ json: { tenants: [{ tenant_id: "northwind", name: "Northwind" }] } });
  });
  await page.route(`${api}/workflow-runs`, async (route) => {
    await route.fulfill({ json: { workflow_runs: [] } });
  });
  await page.route(`${api}/approvals`, async (route) => {
    await route.fulfill({ json: { approvals: [current] } });
  });
  await page.route(`${api}/workflow-runs/${runId}`, async (route) => {
    await route.fulfill({ json: workflow() });
  });
  await page.route(`${api}/approvals/${approvalId}`, async (route) => {
    await route.fulfill({ json: current });
  });
}

async function openReview(page: Page, role: string) {
  await page.addInitScript((storedRole) => {
    window.localStorage.setItem("opsmesh.role", storedRole);
  }, role);
  await page.goto(`/approvals/${approvalId}`);
  await page.getByTestId("local-role").selectOption(role);
}

test("manager approves the proposal bound to its hash", async ({ page }) => {
  let posted: { decision?: string } | undefined;
  let postedAuthorization = "";
  await installRoutes(page);
  await page.route(`${api}/approvals/${approvalId}/decision`, async (route: Route) => {
    posted = route.request().postDataJSON() as { decision?: string };
    postedAuthorization = route.request().headers().authorization ?? "";
    await route.fulfill({
      json: approval({
        status: "APPROVED",
        approved_by: "manager@northwind.example",
        proposal_status: "APPROVED",
        proposal_approvals: [
          {
            id: approvalId,
            required_role: "procurement_manager",
            status: "APPROVED",
            arguments_hash: liveHash,
            live_arguments_hash: liveHash,
            approved_by: "manager@northwind.example",
            expires_at: "2026-10-06T18:00:00Z",
          },
        ],
      }),
    });
  });
  await openReview(page, "procurement_manager");
  await expect(page.getByTestId("proposal-quantity")).toHaveText("250 units");
  await expect(page.getByTestId("approve-proposal")).toBeEnabled();
  await expect(page.getByTestId("approval-hash")).toHaveAttribute("title", liveHash);
  await page.getByTestId("approve-proposal").click();
  await expect(page.getByTestId("approve-dialog")).toContainText("250 units");
  await expect(page.getByTestId("approve-dialog")).toContainText("$9,250.00");
  await page.getByTestId("confirm-approve").click();
  await expect(page.getByTestId("status-badge").first()).toContainText("Approved");
  await expect(page.getByText("Decided by manager@northwind.example")).toBeVisible();
  expect(posted?.decision).toBe("approve");
  expect(postedAuthorization).toBe("Bearer token.procurement_manager");
});

test("manager rejects with a comment", async ({ page }) => {
  let posted: { decision?: string; comment?: string } | undefined;
  await installRoutes(page);
  await page.route(`${api}/approvals/${approvalId}/decision`, async (route) => {
    posted = route.request().postDataJSON() as { decision?: string; comment?: string };
    await route.fulfill({
      json: approval({
        status: "REJECTED",
        approved_by: "manager@northwind.example",
        proposal_status: "REJECTED",
      }),
    });
  });
  await openReview(page, "procurement_manager");
  await page.getByTestId("reject-proposal").click();
  await expect(page.getByTestId("reject-error")).toHaveText("A comment is required to reject.");
  expect(posted).toBeUndefined();
  await page.getByTestId("reject-comment").fill("Quantity is higher than the shelf plan.");
  await page.getByTestId("reject-proposal").click();
  await expect(page.getByTestId("status-badge").first()).toContainText("Rejected");
  expect(posted?.decision).toBe("reject");
  expect(posted?.comment).toMatch(/shelf plan/);
});

test("a viewer cannot decide the manager approval", async ({ page }) => {
  await installRoutes(page);
  await openReview(page, "viewer");
  await expect(page.getByTestId("approve-proposal")).toBeDisabled();
  await expect(page.getByTestId("reject-proposal")).toBeDisabled();
  await expect(page.getByTestId("decision-reason")).toHaveText("Requires procurement_manager role.");
});

test("a stale hash blocks the decision", async ({ page }) => {
  await installRoutes(
    page,
    approval({
      arguments_hash: staleHash,
      live_arguments_hash: liveHash,
    }),
  );
  await openReview(page, "procurement_manager");
  await expect(page.getByTestId("stale-hash")).toContainText("A new approval is required.");
  await expect(page.getByTestId("approve-proposal")).toBeDisabled();
  await expect(page.getByTestId("approval-hash")).toHaveAttribute("title", liveHash);
  await expect(page.getByText("Approval hash", { exact: false })).toBeVisible();
});
