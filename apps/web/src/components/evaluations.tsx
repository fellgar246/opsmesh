"use client";

import { useQuery } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { ApiError, getLatestEvaluation } from "@/lib/api";
import { costDelta, formatDurationMs, formatMoneyCents, formatRelative, modelCallDelta } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { EvaluationCase, EvaluationRun } from "@/lib/types";

import { ErrorState, LoadingRows } from "./states";

const securityCategories = new Set(["cross_tenant_access", "prompt_injection"]);

const categoryLabels: Record<string, string> = {
  normal_reorder: "Normal reorder",
  insufficient_budget: "Insufficient budget",
  supplier_minimum_order_conflict: "Supplier minimum order",
  unapproved_supplier: "Unapproved supplier",
  forecast_uncertainty: "Forecast uncertainty",
  missing_inventory: "Missing inventory",
  supplier_delay: "Supplier delay",
  conflicting_evidence: "Conflicting evidence",
  policy_approval_required: "Policy approval required",
  cross_tenant_access: "Cross-tenant",
  prompt_injection: "Prompt injection",
  tool_failure: "Tool failure",
  agent_timeout: "Agent timeout",
  partial_workflow_failure: "Partial workflow failure",
};

export function EvaluationsConsole() {
  const { tenantId } = useSession();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const category = params.get("category") ?? "all";
  const query = useQuery({
    queryKey: ["evaluations", tenantId],
    queryFn: () => getLatestEvaluation(tenantId),
  });

  if (query.isLoading) return <LoadingRows />;
  if (query.error instanceof ApiError && query.error.status === 403) {
    return (
      <div data-testid="permission-state" className="rounded-lg border border-line bg-surface px-6 py-10">
        <h1 className="text-xl font-semibold">Evaluations</h1>
        <p className="mt-2 max-w-xl text-muted">You do not have access to evaluation scores.</p>
      </div>
    );
  }
  if (query.error) {
    return (
      <ErrorState
        title="Couldn't load evaluations"
        message={query.error.message}
        onRetry={() => void query.refetch()}
      />
    );
  }
  const run = query.data?.run ?? null;
  if (!run) {
    return (
      <div>
        <h1 className="mb-4 text-xl font-semibold">Evaluations</h1>
        <div data-testid="empty-state" className="rounded-lg border border-line bg-surface px-6 py-10">
          <h2 className="text-base font-semibold">No evaluation runs yet</h2>
          <p className="mt-2 max-w-xl text-muted">
            Run the evaluation suite to compare workflow changes on correctness, policy, and cost.
          </p>
        </div>
      </div>
    );
  }
  return (
    <EvaluationsView
      run={run}
      previous={query.data?.previous ?? null}
      category={category}
      onCategory={(next) => {
        const queryString = next === "all" ? "" : `?category=${encodeURIComponent(next)}`;
        router.replace(`${pathname}${queryString}`);
      }}
    />
  );
}

export function EvaluationsView({
  run,
  previous,
  category,
  onCategory,
}: {
  run: EvaluationRun;
  previous: EvaluationRun | null;
  category: string;
  onCategory: (category: string) => void;
}) {
  const total = run.passed + run.failed;
  const rows = orderedCases(run.cases, category);
  const categories = uniqueCategories(run.cases);
  const modelDelta =
    previous === null ? null : modelCallDelta(run.efficiency.model_calls, previous.efficiency.model_calls);
  const spendDelta =
    previous === null
      ? null
      : costDelta(
          run.efficiency.estimated_cost_per_successful_workflow_cents,
          previous.efficiency.estimated_cost_per_successful_workflow_cents,
        );
  return (
    <div>
      <div className="mb-4">
        <h1 className="text-xl font-semibold">Evaluations</h1>
        <p className="mt-1 text-muted">
          Suite {run.suite_version} · {run.suite} · {formatRelative(run.created_at)}
        </p>
      </div>
      <div className="rounded-lg border border-line bg-surface p-4">
        <p className="text-[12px] text-muted">Passed</p>
        <p data-testid="evaluation-passed" className="tabular text-[28px] leading-9 font-semibold">
          {run.passed} / {total}
        </p>
        {previous ? (
          <p className="mt-1 text-muted">
            Previous run passed {previous.passed} / {previous.passed + previous.failed}.
          </p>
        ) : null}
      </div>
      <h2 className="mt-6 text-sm font-semibold">Deterministic metrics</h2>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Metric label="Correct agents" value={String(run.deterministic.correct_agents)} />
        <Metric label="Unnecessary agents" value={String(run.deterministic.unnecessary_agents)} />
        <Metric label="Correct tools" value={String(run.deterministic.correct_tools)} />
        <Metric label="Forbidden tools" value={String(run.deterministic.forbidden_tools)} />
        <Metric label="Fact accuracy" value={factAccuracy(run)} />
        <Metric label="Constraint violations" value={String(run.deterministic.constraint_violations)} />
        <Metric label="Policy violations" value={String(run.deterministic.policy_violations)} />
        <Metric label="Approval correctness" value={String(run.deterministic.approval_correct)} />
        <Metric label="Cross-tenant failures" value={String(run.deterministic.cross_tenant_failures)} />
        <Metric label="Ceilings respected" value={run.deterministic.ceilings_respected ? "Yes" : "No"} />
      </div>
      <h2 className="mt-6 text-sm font-semibold">Efficiency</h2>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Metric
          label="Task completion"
          value={`${run.efficiency.task_completion} / ${run.efficiency.workflows}`}
        />
        <Metric
          label="Model calls"
          value={String(run.efficiency.model_calls)}
          detail={modelDelta}
        />
        <Metric label="Tool calls" value={String(run.efficiency.tool_calls)} />
        <Metric label="Latency" value={formatDurationMs(run.efficiency.latency_ms)} />
        <Metric
          label="Cost per successful workflow"
          value={formatMoneyCents(run.efficiency.estimated_cost_per_successful_workflow_cents)}
          detail={spendDelta}
        />
      </div>
      <div className="mt-6 flex flex-wrap gap-2" role="group" aria-label="Category">
        <FilterButton active={category === "all"} onClick={() => onCategory("all")} label="All" />
        {categories.map((item) => (
          <FilterButton
            key={item}
            active={category === item}
            onClick={() => onCategory(item)}
            label={categoryLabels[item] ?? item}
          />
        ))}
      </div>
      {rows.length === 0 ? (
        <div className="mt-4">
          <h2 className="text-base font-semibold">No cases match this category</h2>
          <button type="button" className="mt-3 h-8 text-accent" onClick={() => onCategory("all")}>
            Clear filters
          </button>
        </div>
      ) : (
        <div className="mt-4 overflow-auto rounded-lg border border-line">
          <table className="w-full border-collapse text-left text-[13px]">
            <thead className="bg-sunken text-muted">
              <tr>
                {["Case", "Category", "Status"].map((heading) => (
                  <th key={heading} scope="col" className="px-3 py-2 font-medium">
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => (
                <tr
                  key={item.id}
                  data-testid={
                    securityCategories.has(item.category) && !item.passed
                      ? "security-failure"
                      : "evaluation-case"
                  }
                  className="border-t border-line"
                >
                  <td className="px-3 py-2 font-medium">{item.id}</td>
                  <td className="px-3 py-2">{categoryLabels[item.category] ?? item.category}</td>
                  <td className="px-3 py-2">{item.passed ? "Pass" : "Fail"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, detail }: { label: string; value: string; detail?: string | null }) {
  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <p className="text-[12px] text-muted">{label}</p>
      <p className="tabular text-[20px] leading-7 font-semibold">{value}</p>
      {detail ? <p className="mt-1 text-[12px] text-muted">{detail}</p> : null}
    </div>
  );
}

function FilterButton({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`h-8 rounded-md border px-3 ${
        active ? "border-accent bg-selected text-ink" : "border-line text-muted"
      }`}
    >
      {label}
    </button>
  );
}

function factAccuracy(run: EvaluationRun): string {
  const { matched, total } = run.deterministic.fact_accuracy;
  if (total === 0) return "—";
  return `${matched} / ${total}`;
}

function uniqueCategories(cases: EvaluationCase[]): string[] {
  const found: string[] = [];
  for (const item of cases) {
    if (!found.includes(item.category)) found.push(item.category);
  }
  return found;
}

function orderedCases(cases: EvaluationCase[], category: string): EvaluationCase[] {
  const filtered = category === "all" ? cases : cases.filter((item) => item.category === category);
  return [...filtered].sort((left, right) => rank(left) - rank(right));
}

function rank(item: EvaluationCase): number {
  if (securityCategories.has(item.category) && !item.passed) return 0;
  return 1;
}

