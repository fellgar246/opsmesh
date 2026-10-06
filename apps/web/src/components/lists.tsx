"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { WorkflowTable } from "@/components/operations-console";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { workflowOutcome, workflowTitle } from "@/components/workflow-detail";
import { getCost, getLatestEvaluation, listWorkflows } from "@/lib/api";
import { formatMoneyCents, formatRelative, modelCallDelta } from "@/lib/format";
import { proposalStatusLabels, workflowTypeLabels } from "@/lib/labels";
import { useSession } from "@/lib/session";
import type { UsageLedger } from "@/lib/types";

function useRuns() {
  const { tenantId } = useSession();
  return useQuery({
    queryKey: ["workflows", tenantId],
    queryFn: () => listWorkflows(tenantId),
  });
}

export function WorkflowList() {
  const query = useRuns();
  if (query.isLoading) return <LoadingRows />;
  if (query.error) {
    return (
      <ErrorState
        title="Couldn't load workflows"
        message={query.error.message}
        onRetry={() => void query.refetch()}
      />
    );
  }
  const runs = query.data?.workflow_runs ?? [];
  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Workflows</h1>
        <Link href="/operations" className="inline-flex h-8 items-center rounded-md bg-accent px-3 text-accent-fg">
          New workflow
        </Link>
      </div>
      {runs.length === 0 ? (
        <EmptyState
          title="No workflows yet"
          body="Run a reorder recommendation to see agents, evidence and the proposal here."
        />
      ) : (
        <WorkflowTable runs={runs} />
      )}
    </div>
  );
}

export function ProposalList() {
  const query = useRuns();
  if (query.isLoading) return <LoadingRows />;
  if (query.error) {
    return (
      <ErrorState title="Couldn't load proposals" message={query.error.message} onRetry={() => void query.refetch()} />
    );
  }
  const proposals = (query.data?.workflow_runs ?? []).flatMap((run) =>
    run.proposals
      .filter((proposal) => proposal.status !== "SUPERSEDED")
      .map((proposal) => ({ proposal, run })),
  );
  return (
    <div>
      <h1 className="text-xl font-semibold">Proposals</h1>
      {proposals.length === 0 ? (
        <div className="mt-4">
          <EmptyState
            title="No proposals yet"
            body="Run a reorder recommendation to see agents, evidence and the proposal here."
          />
        </div>
      ) : (
        <div className="mt-4 overflow-auto rounded-lg border border-line">
          <table className="w-full border-collapse text-left text-[13px]">
            <thead className="bg-sunken text-muted">
              <tr>
                {["Status", "Proposal", "Extended cost", "Risk level", "Workflow", "Created"].map((heading) => (
                  <th key={heading} scope="col" className="px-3 py-2 font-medium">
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {proposals.map(({ proposal, run }) => {
                const status = proposalStatusLabels[proposal.status] ?? {
                  label: proposal.status,
                  tone: "neutral",
                };
                const cost = proposal.arguments.extended_cost_cents;
                return (
                  <tr key={proposal.id} className="border-t border-line">
                    <td className="px-3 py-2">
                      <StatusBadge status={proposal.status} label={status.label} tone={status.tone} />
                    </td>
                    <td className="px-3 py-2">{workflowOutcome(run)}</td>
                    <td className="px-3 py-2 text-right tabular">
                      {typeof cost === "number" ? formatMoneyCents(cost) : "—"}
                    </td>
                    <td className="px-3 py-2">{proposal.risk_level}</td>
                    <td className="px-3 py-2">
                      <Link href={`/workflows/${run.id}`} className="text-accent">
                        {workflowTitle(run)}
                      </Link>
                    </td>
                    <td className="px-3 py-2">{formatRelative(proposal.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function CostPage() {
  const { tenantId } = useSession();
  const query = useQuery({
    queryKey: ["cost", tenantId],
    queryFn: () => getCost(tenantId),
  });
  const evaluation = useQuery({
    queryKey: ["evaluations", tenantId],
    queryFn: () => getLatestEvaluation(tenantId),
  });
  if (query.isLoading) return <LoadingRows />;
  if (query.error) {
    return <ErrorState title="Couldn't load cost" message={query.error.message} onRetry={() => void query.refetch()} />;
  }
  const rows = query.data?.workflows ?? [];
  const modelCalls = rows.reduce((sum, row) => sum + row.model_calls, 0);
  const unknown = rows.some((row) => row.model_calls > 0 && row.estimated_cost_cents === null);
  const cost = rows.reduce((sum, row) => sum + (row.estimated_cost_cents ?? 0), 0);
  const current = evaluation.data?.run ?? null;
  const previous = evaluation.data?.previous ?? null;
  const delta =
    current && previous
      ? modelCallDelta(current.efficiency.model_calls, previous.efficiency.model_calls)
      : null;
  const started = query.data?.workflows_started_today ?? 0;
  const ceiling = query.data?.daily_ceiling ?? 15;
  return (
    <div>
      <h1 className="text-xl font-semibold">Cost</h1>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <Tile label="Workflows today" value={`${started} / ${ceiling}`} />
        <Tile label="Est. cost" value={unknown ? "—" : formatMoneyCents(cost)} />
        <Tile label="Model calls" value={String(modelCalls)} detail={delta ? `Last evaluation ${delta}` : null} />
      </div>
      {modelCalls === 0 ? (
        <p data-testid="cost-note" className="mt-4 text-muted">
          No model calls recorded. Local deterministic runs cost $0.
        </p>
      ) : null}
      {delta ? (
        <p data-testid="evaluation-model-delta" className="mt-4 text-muted">
          Last evaluation {delta}
        </p>
      ) : null}
      {rows.length === 0 ? (
        <p className="mt-4 tabular text-[28px] font-semibold">0</p>
      ) : (
        <div className="mt-4 overflow-auto rounded-lg border border-line">
          <table className="w-full border-collapse text-left text-[13px]">
            <thead className="bg-sunken text-muted">
              <tr>
                {["Workflow", "Model calls", "Tool calls", "Input tokens", "Output tokens", "Est. cost"].map(
                  (heading) => (
                    <th key={heading} scope="col" className="px-3 py-2 font-medium">
                      {heading}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.workflow_id} className="border-t border-line">
                  <td className="px-3 py-2">
                    <Link href={`/workflows/${row.workflow_id}`} className="text-accent">
                      {ledgerTitle(row)}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-right tabular">{row.model_calls}</td>
                  <td className="px-3 py-2 text-right tabular">{row.tool_calls}</td>
                  <td className="px-3 py-2 text-right tabular">{row.input_tokens}</td>
                  <td className="px-3 py-2 text-right tabular">{row.output_tokens}</td>
                  <td className="px-3 py-2 text-right tabular">
                    {formatMoneyCents(row.estimated_cost_cents)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Tile({ label, value, detail }: { label: string; value: string; detail?: string | null }) {
  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <p className="text-[12px] text-muted">{label}</p>
      <p className="tabular text-[28px] leading-9 font-semibold">{value}</p>
      {detail ? <p className="mt-1 text-[12px] text-muted">{detail}</p> : null}
    </div>
  );
}

function ledgerTitle(row: UsageLedger): string {
  const type = workflowTypeLabels[row.workflow_type] ?? row.workflow_type;
  const sku = typeof row.input.sku === "string" ? row.input.sku : "";
  const region = typeof row.input.region === "string" ? row.input.region : "";
  const supplier = typeof row.input.supplier_id === "string" ? row.input.supplier_id : "";
  return [type, sku, region, supplier].filter(Boolean).join(" · ");
}
