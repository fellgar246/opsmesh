import { expect, test } from "@playwright/test";

const runId = "11111111-1111-4111-8111-111111111111";

test("golden reorder shows quantity and manager approval", async ({ page }) => {
  await page.route("**/tenants", async (route) => {
    await route.fulfill({
      json: { tenants: [{ tenant_id: "northwind", name: "Northwind" }] },
    });
  });
  await page.route("**/tenants/northwind/workflow-runs", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { workflow_runs: [] } });
      return;
    }
    await route.continue();
  });
  await page.route("**/operations/reorder", async (route) => {
    await route.fulfill({
      json: {
        run_id: runId,
        status: "WAITING_APPROVAL",
        quantity: 250,
        requires_manager_approval: true,
        requires_finance_approval: false,
      },
    });
  });
  await page.route(`**/workflow-runs/${runId}`, async (route) => {
    await route.fulfill({
      json: {
        id: runId,
        tenant_id: "northwind",
        workflow_type: "reorder",
        requested_by: "analyst@northwind.example",
        status: "WAITING_APPROVAL",
        input: { sku: "ABC-42", region: "mx-central", service_level: 0.9 },
        started_at: "2026-09-23T18:00:00Z",
        finished_at: "2026-09-23T18:00:01Z",
        cost_summary: {
          model_calls: 0,
          tool_calls: 6,
          latency_ms: 40,
          estimated_cost_cents: 0,
          supervisor_iterations: 1,
        },
        plan: {
          reason: "Check inventory, demand, and supplier facts, then finance, then policy.",
          tasks: [
            { id: "inventory", agent: "inventory", input: { sku: "ABC-42" }, read_only: true },
            { id: "finance", agent: "finance", input: {}, read_only: true },
          ],
          dependencies: [{ task_id: "finance", depends_on: "inventory" }],
        },
        tasks: [],
        evidence: [],
        proposals: [
          {
            id: "55555555-5555-4555-8555-555555555555",
            workflow_run_id: runId,
            proposal_type: "reorder",
            arguments: { quantity: 250, extended_cost_cents: 925000 },
            estimated_impact: {},
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
      },
    });
  });

  await page.goto("/operations");
  await page.getByLabel("SKU").fill("ABC-42");
  await page.getByLabel("Region").fill("mx-central");
  await page.getByLabel("Service level").fill("0.90");
  await page.getByRole("button", { name: "Run workflow" }).click();
  await expect(page.getByTestId("proposal-quantity")).toHaveText("250 units");
  await expect(page.getByTestId("supervisor-plan-reason")).toHaveText(
    "Check inventory, demand, and supplier facts, then finance, then policy.",
  );
  await expect(page.getByTestId("approval-role")).toContainText("Manager approval required");
  await expect(page.getByTestId("status-badge").first()).toContainText("Waiting approval");
});
