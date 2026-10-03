"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { FactGrid } from "@/components/fact-card";
import { Infeasibility, ProposalSummary } from "@/components/proposal-summary";
import { ErrorState, LoadingRows, NotFoundState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { TaskGraph } from "@/components/task-graph";
import { ApiError, getWorkflow } from "@/lib/api";
import {
  durationBetween,
  formatAbsoluteUtc,
  formatDurationMs,
  formatMoneyCents,
  formatRelative,
  isUuid,
  readString,
  truncateMiddle,
} from "@/lib/format";
import { workflowCeilings } from "@/lib/identity";
import { agentLabels, workflowStatusLabels, workflowTypeLabels } from "@/lib/labels";
import { useSession } from "@/lib/session";
import { terminalStatuses, type Proposal, type SupervisorPlan, type WorkflowRun } from "@/lib/types";

const tabs = ["overview", "graph", "evidence", "trace", "cost"] as const;

export function WorkflowDetail({ runId }: { runId: string }) {
  const { tenantId, tenantName } = useSession();
  const [tab, setTab] = useState<(typeof tabs)[number]>("overview");
  const [inputOpen, setInputOpen] = useState(false);
  const valid = isUuid(runId);
  const query = useQuery({
    queryKey: ["workflow", tenantId, runId],
    enabled: valid,
    queryFn: () => getWorkflow(tenantId, runId),
    refetchInterval: (current) => {
      const status = current.state.data?.status;
      if (!status || terminalStatuses.has(status)) return false;
      return 2000;
    },
  });

  if (!valid) return <NotFoundState tenantName={tenantName} />;
  if (query.isLoading) return <LoadingRows />;
  if (query.error instanceof ApiError && query.error.status === 404) {
    return <NotFoundState tenantName={tenantName} />;
  }
  if (query.error) {
    return (
      <ErrorState
        title="Couldn't load this workflow"
        message={query.error.message}
        onRetry={() => void query.refetch()}
      />
    );
  }
  if (!query.data) return <NotFoundState tenantName={tenantName} />;
  return (
    <WorkflowBody
      run={query.data}
      tab={tab}
      setTab={setTab}
      inputOpen={inputOpen}
      setInputOpen={setInputOpen}
      refreshing={query.isFetching && !query.isLoading}
    />
  );
}

function WorkflowBody({
  run,
  tab,
  setTab,
  inputOpen,
  setInputOpen,
  refreshing,
}: {
  run: WorkflowRun;
  tab: (typeof tabs)[number];
  setTab: (tab: (typeof tabs)[number]) => void;
  inputOpen: boolean;
  setInputOpen: (open: boolean) => void;
  refreshing: boolean;
}) {
  const status = workflowStatusLabels[run.status] ?? { label: run.status, tone: "neutral" };
  const proposal = activeProposal(run);
  const reason = policyReason(run);
  const explanation = policyExplanation(run);
  const title = workflowTitle(run);
  return (
    <article aria-live="polite">
      <p className="text-[12px] text-muted">
        <Link href="/workflows" className="text-accent">
          Workflows
        </Link>
        <span> / </span>
        <span className="font-mono" title={run.id}>
          {truncateMiddle(run.id, 8, 4)}
        </span>
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">{title}</h1>
        <StatusBadge status={run.status} label={status.label} tone={status.tone} />
        {refreshing ? <span className="text-[12px] text-muted">Updating</span> : null}
      </div>
      <p className="mt-2 text-[13px] text-muted">
        Requested by {run.requested_by} · started{" "}
        <time dateTime={run.started_at} title={formatAbsoluteUtc(run.started_at)}>
          {formatRelative(run.started_at)}
        </time>{" "}
        · duration {durationBetween(run.started_at, run.finished_at)}
      </p>
      {run.status === "FAILED" && reason ? (
        <div className="mt-4">
          <Infeasibility reason={reason} explanation={explanation} />
        </div>
      ) : null}
      {run.plan ? <PlanPanel plan={run.plan} /> : null}
      <div className="mt-4">
        {proposal ? <ProposalSummary proposal={proposal} /> : reason && run.status !== "FAILED" ? (
          <Infeasibility reason={reason} explanation={explanation} />
        ) : !proposal && !reason ? (
          <p className="text-muted">No proposal was recorded.</p>
        ) : null}
      </div>
      <div className="mt-6 flex flex-wrap gap-2" role="tablist">
        {tabs.map((item) => (
          <button
            key={item}
            type="button"
            role="tab"
            aria-selected={tab === item}
            className={`h-8 rounded-md px-3 capitalize ${tab === item ? "bg-selected" : "text-muted"}`}
            onClick={() => setTab(item)}
          >
            {item}
          </button>
        ))}
      </div>
      <div className="mt-4" role="tabpanel">
        {tab === "overview" ? <FactGrid tasks={run.tasks} /> : null}
        {tab === "graph" ? <TaskGraph tasks={run.tasks} /> : null}
        {tab === "evidence" ? <EvidenceTable run={run} /> : null}
        {tab === "trace" ? <TraceList run={run} /> : null}
        {tab === "cost" ? <CostPanel run={run} /> : null}
      </div>
      <section className="mt-8">
        <button type="button" className="text-sm font-semibold" onClick={() => setInputOpen(!inputOpen)}>
          {inputOpen ? "Hide input" : "Show input"}
        </button>
        {inputOpen ? (
          <pre className="mt-2 overflow-auto rounded-lg bg-sunken p-3 font-mono text-[13px]">
            {JSON.stringify(run.input, null, 2)}
          </pre>
        ) : null}
      </section>
    </article>
  );
}

function PlanPanel({ plan }: { plan: SupervisorPlan }) {
  const dependencies = new Map<string, string[]>();
  for (const task of plan.tasks) dependencies.set(task.id, []);
  for (const edge of plan.dependencies) {
    const current = dependencies.get(edge.task_id) ?? [];
    current.push(edge.depends_on);
    dependencies.set(edge.task_id, current);
  }
  return (
    <section aria-label="Supervisor plan" className="mt-4 rounded-lg border border-line bg-surface p-4" data-testid="supervisor-plan">
      <h2 className="text-sm font-semibold">Supervisor plan</h2>
      <p className="mt-1 text-[13px]" data-testid="supervisor-plan-reason">
        {plan.reason}
      </p>
      <ul className="mt-3 space-y-1 text-[13px]">
        {plan.tasks.map((task) => {
          const deps = dependencies.get(task.id) ?? [];
          const waiting = deps.length === 0 ? "no dependencies" : `depends on ${deps.join(", ")}`;
          return (
            <li key={task.id}>
              <span className="font-medium">{agentLabels[task.agent] ?? task.agent}</span>
              <span className="text-muted"> · {waiting}</span>
              {task.read_only ? <span className="text-muted"> · read-only</span> : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function policyReason(run: WorkflowRun): string | null {
  const policy = run.tasks.find((task) => task.agent_type === "policy");
  const facts = policy?.results[0]?.facts;
  if (!facts) return null;
  const records = facts.records;
  if (Array.isArray(records)) {
    for (const record of records) {
      if (record && typeof record === "object" && "reason" in record) {
        return readString((record as { reason?: unknown }).reason);
      }
    }
    return null;
  }
  return readString(facts.reason);
}

export function workflowTitle(run: WorkflowRun): string {
  const type = workflowTypeLabels[run.workflow_type] ?? run.workflow_type;
  const sku = readString(run.input.sku);
  const region = readString(run.input.region);
  const supplier = readString(run.input.supplier_id);
  const parts = [type, sku, region, supplier].filter(Boolean);
  return parts.join(" · ");
}

export function activeProposal(run: WorkflowRun): Proposal | undefined {
  return run.proposals.find((item) => item.status !== "SUPERSEDED") ?? run.proposals.at(-1);
}

function policyExplanation(run: WorkflowRun): string | null {
  const policy = run.tasks.find((task) => task.agent_type === "policy");
  return readString(policy?.results[0]?.facts.explanation);
}

export function workflowOutcome(run: WorkflowRun): string {
  const proposal = activeProposal(run);
  const quantity = proposal?.arguments.quantity;
  if (typeof quantity === "number") return `${quantity} units`;
  const reason = readString(proposal?.arguments.reason) ?? policyReason(run);
  if (reason) return reason;
  return "—";
}

function EvidenceTable({ run }: { run: WorkflowRun }) {
  if (run.evidence.length === 0) return <p className="text-muted">No evidence was recorded.</p>;
  return (
    <div className="overflow-auto rounded-lg border border-line">
      <table className="w-full border-collapse text-left text-[13px]">
        <thead className="bg-sunken text-muted">
          <tr>
            {["Type", "Source", "Record", "Observed", "Checksum"].map((heading) => (
              <th key={heading} scope="col" className="px-3 py-2 font-medium">
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {run.evidence.map((item) => (
            <tr key={item.id} className="border-t border-line">
              <td className="px-3 py-2">{item.type}</td>
              <td className="px-3 py-2">{item.source}</td>
              <td className="px-3 py-2 font-mono">{item.source_record_id}</td>
              <td className="px-3 py-2">
                <time dateTime={item.observed_at} title={formatAbsoluteUtc(item.observed_at)}>
                  {formatRelative(item.observed_at)}
                </time>
              </td>
              <td className="px-3 py-2 font-mono" title={item.checksum}>
                {item.checksum.slice(0, 12)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TraceList({ run }: { run: WorkflowRun }) {
  return (
    <ul className="space-y-2">
      {run.tasks.map((task) => (
        <li key={task.id} className="rounded-lg border border-line bg-surface px-3 py-2">
          <span className="font-medium">{task.agent_type}</span>
          <span className="ml-2 text-muted">{task.status}</span>
          <span className="ml-2 text-muted">
            {durationBetween(task.started_at ?? run.started_at, task.finished_at)}
          </span>
        </li>
      ))}
    </ul>
  );
}

function CostPanel({ run }: { run: WorkflowRun }) {
  const summary = run.cost_summary;
  const durationSeconds = summary.latency_ms / 1000;
  return (
    <div className="space-y-3">
      <Meter label="Tasks" value={run.tasks.length} ceiling={workflowCeilings.tasks} />
      <Meter label="Model calls" value={summary.model_calls} ceiling={workflowCeilings.modelCalls} />
      <Meter
        label="Supervisor iterations"
        value={summary.supervisor_iterations ?? 0}
        ceiling={workflowCeilings.supervisorIterations}
      />
      <Meter label="Tool calls" value={summary.tool_calls} ceiling={workflowCeilings.toolCalls} />
      <Meter
        label="Duration"
        value={Math.round(durationSeconds)}
        ceiling={workflowCeilings.durationSeconds}
        suffix="s"
      />
      <p className="text-muted">
        Estimated cost {formatMoneyCents(summary.estimated_cost_cents)} · latency{" "}
        {formatDurationMs(summary.latency_ms)}
      </p>
    </div>
  );
}

function Meter({
  label,
  value,
  ceiling,
  suffix = "",
}: {
  label: string;
  value: number;
  ceiling: number;
  suffix?: string;
}) {
  const ratio = ceiling === 0 ? 0 : Math.min(1, value / ceiling);
  const tone = ratio >= 1 ? "var(--danger-fg)" : ratio >= 0.8 ? "var(--warning-fg)" : "var(--accent)";
  return (
    <div>
      <div className="mb-1 flex justify-between text-[13px]">
        <span>{label}</span>
        <span className="tabular">
          {value}
          {suffix} / {ceiling}
          {suffix}
        </span>
      </div>
      <div className="h-1.5 rounded bg-sunken">
        <div className="h-1.5 rounded" style={{ width: `${ratio * 100}%`, background: tone }} />
      </div>
    </div>
  );
}
