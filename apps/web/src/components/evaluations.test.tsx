import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { EvaluationsView } from "@/components/evaluations";
import type { EvaluationRun } from "@/lib/types";

function sample(overrides: Partial<EvaluationRun> = {}): EvaluationRun {
  return {
    id: "run-1",
    tenant_id: "northwind",
    suite_version: "1",
    suite: "smoke",
    created_at: "2026-10-03T18:00:00Z",
    passed: 12,
    failed: 2,
    deterministic: {
      correct_agents: 14,
      unnecessary_agents: 0,
      correct_tools: 14,
      forbidden_tools: 1,
      fact_accuracy: { matched: 20, total: 22 },
      constraint_violations: 0,
      policy_violations: 0,
      approval_correct: 13,
      cross_tenant_failures: 1,
      ceilings_respected: true,
    },
    efficiency: {
      task_completion: 9,
      workflows: 14,
      model_calls: 4,
      tool_calls: 40,
      latency_ms: 900,
      estimated_cost_per_successful_workflow_cents: 0,
    },
    cases: [
      goldenCase("gw-normal-reorder-01", "normal_reorder", true),
      goldenCase("gw-cross-tenant-access-01", "cross_tenant_access", false),
      goldenCase("gw-prompt-injection-01", "prompt_injection", false),
      goldenCase("gw-tool-failure-01", "tool_failure", true),
    ],
    hosted_ingested: false,
    ...overrides,
  };
}

function goldenCase(id: string, category: string, passed: boolean) {
  return {
    id,
    category,
    tenant_id: "northwind",
    passed,
    failures: passed ? [] : ["cross_tenant"],
    status: passed ? "COMPLETED" : "FAILED",
    model_calls: 0,
    tool_calls: 1,
    latency_ms: 10,
    estimated_cost_cents: 0,
  };
}

function Harness({ run, previous = null }: { run: EvaluationRun; previous?: EvaluationRun | null }) {
  const [category, setCategory] = useState("all");
  return (
    <EvaluationsView run={run} previous={previous} category={category} onCategory={setCategory} />
  );
}

describe("evaluation scores", () => {
  it("shows the latest passed count, metrics, and a better model-call delta", () => {
    const previous = sample({
      id: "run-0",
      efficiency: {
        ...sample().efficiency,
        model_calls: 7,
        estimated_cost_per_successful_workflow_cents: 40,
      },
    });
    render(<Harness run={sample()} previous={previous} />);
    expect(screen.getByTestId("evaluation-passed")).toHaveTextContent("12 / 14");
    expect(screen.getByText("Deterministic metrics")).toBeInTheDocument();
    expect(screen.getByText("Efficiency")).toBeInTheDocument();
    expect(screen.getByText("20 / 22")).toBeInTheDocument();
    expect(screen.getByText("▼ 3 model calls (better)")).toBeInTheDocument();
    expect(screen.getByText("▼ $0.40 (better)")).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveAttribute("data-testid", "security-failure");
    expect(rows[1]).toHaveAttribute("data-testid", "security-failure");
  });

  it("filters cases by category", async () => {
    const user = userEvent.setup();
    render(<Harness run={sample()} />);
    await user.click(screen.getByRole("button", { name: "Tool failure" }));
    expect(screen.getByText("gw-tool-failure-01")).toBeInTheDocument();
    expect(screen.queryByText("gw-normal-reorder-01")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "All" }));
    expect(screen.getByText("gw-normal-reorder-01")).toBeInTheDocument();
  });

  it("clears a category that has no cases", async () => {
    const user = userEvent.setup();
    render(<FilteredEmpty />);
    expect(screen.getByText("No cases match this category")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.getByText("gw-normal-reorder-01")).toBeInTheDocument();
  });
});

function FilteredEmpty() {
  const [category, setCategory] = useState("missing_inventory");
  return (
    <EvaluationsView
      run={sample()}
      previous={null}
      category={category}
      onCategory={setCategory}
    />
  );
}
