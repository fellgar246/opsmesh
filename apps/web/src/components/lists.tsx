"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { WorkflowTable } from "@/components/operations-console";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { workflowOutcome, workflowTitle } from "@/components/workflow-detail";
import { listWorkflows } from "@/lib/api";
import { formatMoneyCents, formatRelative } from "@/lib/format";
import { workflowCeilings } from "@/lib/identity";
import { approvalStatusLabels, proposalStatusLabels, roleLabels } from "@/lib/labels";
import { useSession } from "@/lib/session";
import type { WorkflowRun } from "@/lib/types";

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

export function ApprovalList() {
  const query = useRuns();
  if (query.isLoading) return <LoadingRows />;
  if (query.error) {
    return (
      <ErrorState title="Couldn't load approvals" message={query.error.message} onRetry={() => void query.refetch()} />
    );
  }
  const rows = (query.data?.workflow_runs ?? []).flatMap((run) =>
    run.proposals.flatMap((proposal) =>
      proposal.approvals.map((approval) => ({ approval, proposal, run })),
    ),
  );
  return (
    <div>
      <h1 className="text-xl font-semibold">Approvals</h1>
      <p className="mt-2 max-w-xl text-muted">
        This page lists persisted approvals. Decisions are not available yet.
      </p>
      {rows.length === 0 ? (
        <div className="mt-4">
          <EmptyState
            title="No approvals yet"
            body="Run a reorder recommendation to see agents, evidence and the proposal here."
          />
        </div>
      ) : (
        <div className="mt-4 overflow-auto rounded-lg border border-line">
          <table className="w-full border-collapse text-left text-[13px]">
            <thead className="bg-sunken text-muted">
              <tr>
                {["Proposal", "Required role", "Status", "Workflow"].map((heading) => (
                  <th key={heading} scope="col" className="px-3 py-2 font-medium">
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(({ approval, run }) => {
                const status = approvalStatusLabels[approval.status] ?? {
                  label: approval.status,
                  tone: "neutral",
                };
                return (
                  <tr key={approval.id} className="border-t border-line">
                    <td className="px-3 py-2">{workflowOutcome(run)}</td>
                    <td className="px-3 py-2">{roleLabels[approval.required_role] ?? approval.required_role}</td>
                    <td className="px-3 py-2">
                      <StatusBadge status={approval.status} label={status.label} tone={status.tone} />
                    </td>
                    <td className="px-3 py-2">
                      <Link href={`/workflows/${run.id}`} className="text-accent">
                        Open workflow
                      </Link>
                    </td>
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
  const query = useRuns();
  if (query.isLoading) return <LoadingRows />;
  if (query.error) {
    return <ErrorState title="Couldn't load cost" message={query.error.message} onRetry={() => void query.refetch()} />;
  }
  const runs = query.data?.workflow_runs ?? [];
  const modelCalls = runs.reduce((sum, run) => sum + run.cost_summary.model_calls, 0);
  const cost = runs.reduce((sum, run) => sum + (run.cost_summary.estimated_cost_cents ?? 0), 0);
  const unknown = runs.some((run) => run.cost_summary.estimated_cost_cents === null);
  return (
    <div>
      <h1 className="text-xl font-semibold">Cost</h1>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <Tile label="Workflows today" value={`${runsStartedToday(runs)} / ${workflowCeilings.workflowsPerDay}`} />
        <Tile label="Est. cost" value={unknown && cost === 0 ? "—" : formatMoneyCents(cost)} />
        <Tile label="Model calls" value={String(modelCalls)} />
      </div>
      {modelCalls === 0 ? (
        <p data-testid="cost-note" className="mt-4 text-muted">
          No model calls recorded. Local deterministic runs cost $0.
        </p>
      ) : null}
      {runs.length === 0 ? (
        <p className="mt-4 tabular text-[28px] font-semibold">0</p>
      ) : (
        <div className="mt-4 overflow-auto rounded-lg border border-line">
          <table className="w-full border-collapse text-left text-[13px]">
            <thead className="bg-sunken text-muted">
              <tr>
                {["Workflow", "Model calls", "Tool calls", "Est. cost"].map((heading) => (
                  <th key={heading} scope="col" className="px-3 py-2 font-medium">
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id} className="border-t border-line">
                  <td className="px-3 py-2">
                    <Link href={`/workflows/${run.id}`} className="text-accent">
                      {workflowTitle(run)}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-right tabular">{run.cost_summary.model_calls}</td>
                  <td className="px-3 py-2 text-right tabular">{run.cost_summary.tool_calls}</td>
                  <td className="px-3 py-2 text-right tabular">
                    {formatMoneyCents(run.cost_summary.estimated_cost_cents)}
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

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <p className="text-[12px] text-muted">{label}</p>
      <p className="tabular text-[28px] leading-9 font-semibold">{value}</p>
    </div>
  );
}

function runsStartedToday(runs: WorkflowRun[]): number {
  const now = new Date();
  return runs.filter((run) => {
    const started = new Date(run.started_at);
    return (
      started.getUTCFullYear() === now.getUTCFullYear() &&
      started.getUTCMonth() === now.getUTCMonth() &&
      started.getUTCDate() === now.getUTCDate()
    );
  }).length;
}
