"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { z } from "zod";

import { ErrorState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { workflowOutcome, workflowTitle } from "@/components/workflow-detail";
import { ApiError, listWorkflows, startBudgetPlan, startReorder, startSupplierDelay } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { localIdentity, workflowCeilings } from "@/lib/identity";
import { workflowStatusLabels } from "@/lib/labels";
import { useSession } from "@/lib/session";

const reorderSchema = z.object({
  sku: z.string().trim().min(1, "SKU is required."),
  region: z.string().trim().min(1, "Region is required."),
  serviceLevel: z.number().min(0, "Service level must be between 0 and 1.").max(1, "Service level must be between 0 and 1."),
});

type WorkflowKind = "reorder" | "supplier_delay" | "budget_plan";

export function OperationsConsole() {
  const { tenantId } = useSession();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<WorkflowKind>("reorder");
  const [sku, setSku] = useState("");
  const [region, setRegion] = useState("");
  const [serviceLevel, setServiceLevel] = useState("");
  const [supplierId, setSupplierId] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const workflows = useQuery({
    queryKey: ["workflows", tenantId],
    queryFn: () => listWorkflows(tenantId),
  });

  const todayCount = (workflows.data?.workflow_runs ?? []).filter((run) => {
    const started = new Date(run.started_at);
    const now = new Date();
    return (
      started.getUTCFullYear() === now.getUTCFullYear() &&
      started.getUTCMonth() === now.getUTCMonth() &&
      started.getUTCDate() === now.getUTCDate()
    );
  }).length;
  const limitReached = todayCount >= workflowCeilings.workflowsPerDay;

  const mutation = useMutation({
    mutationFn: async () => {
      if (kind === "reorder") {
        const parsed = reorderSchema.safeParse({
          sku,
          region,
          serviceLevel: Number(serviceLevel),
        });
        if (!parsed.success) {
          const errors: Record<string, string> = {};
          for (const issue of parsed.error.issues) {
            const key = String(issue.path[0] ?? "form");
            errors[key === "serviceLevel" ? "service_level" : key] = issue.message;
          }
          throw new ApiError(422, Object.values(errors).join(" "), errors);
        }
        return startReorder(tenantId, {
          requested_by: localIdentity.requestedBy,
          sku: parsed.data.sku,
          region: parsed.data.region,
          service_level: parsed.data.serviceLevel,
        });
      }
      if (kind === "supplier_delay") {
        if (!supplierId.trim()) {
          throw new ApiError(422, "Supplier is required.", { supplier_id: "Supplier is required." });
        }
        return startSupplierDelay(tenantId, {
          requested_by: localIdentity.requestedBy,
          supplier_id: supplierId.trim(),
        });
      }
      const parsed = reorderSchema.safeParse({
        sku,
        region,
        serviceLevel: Number(serviceLevel),
      });
      if (!parsed.success) {
        throw new ApiError(422, "SKU, region, and service level are required.", {
          candidates: "SKU, region, and service level are required.",
        });
      }
      return startBudgetPlan(tenantId, {
        requested_by: localIdentity.requestedBy,
        candidates: [
          {
            sku: parsed.data.sku,
            region: parsed.data.region,
            service_level: parsed.data.serviceLevel,
          },
        ],
      });
    },
    onSuccess: async (result) => {
      setFieldErrors({});
      setFormError(null);
      setToast("Workflow started");
      await queryClient.invalidateQueries({ queryKey: ["workflows", tenantId] });
      router.push(`/workflows/${result.run_id}`);
    },
    onError: (error) => {
      if (error instanceof ApiError) {
        setFieldErrors(error.fieldErrors);
        setFormError(error.message);
        return;
      }
      setFormError("Couldn't reach the API. Retrying…");
    },
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (limitReached || mutation.isPending) return;
    mutation.mutate();
  }

  return (
    <div>
      <h1 className="text-xl font-semibold">Operations</h1>
      <p className="mt-1 text-muted">Start a workflow for {tenantId}.</p>
      {workflows.error ? (
        <div className="mt-4">
          <ErrorState
            title="Couldn't load recent workflows"
            message={workflows.error.message}
            onRetry={() => void workflows.refetch()}
          />
        </div>
      ) : null}
      {limitReached ? (
        <p role="status" className="mt-4 rounded-lg bg-[var(--danger-bg)] px-3 py-2 text-[var(--danger-fg)]">
          Daily workflow limit reached. Resets at 00:00 UTC.
        </p>
      ) : null}
      <div className="mt-6 grid gap-3 lg:grid-cols-3">
        <KindCard
          title="Reorder recommendation"
          body="Recommend a purchase quantity from persisted inventory, demand, supplier, and budget facts."
          selected={kind === "reorder"}
          onSelect={() => setKind("reorder")}
        />
        <KindCard
          title="Supplier delay impact"
          body="Inspect open orders and stockout risk for a delayed supplier."
          selected={kind === "supplier_delay"}
          onSelect={() => setKind("supplier_delay")}
        />
        <KindCard
          title="Budget-aware purchase plan"
          body="Fund the highest stockout risk within the remaining budget."
          selected={kind === "budget_plan"}
          onSelect={() => setKind("budget_plan")}
        />
      </div>
      <form
        data-testid="reorder-form"
        onSubmit={onSubmit}
        className="mt-6 max-w-[720px] space-y-4 rounded-lg border border-line bg-surface p-4"
      >
        {formError ? <ErrorState title="Workflow was not started" message={formError} /> : null}
        {kind === "supplier_delay" ? (
          <Field
            label="Supplier"
            value={supplierId}
            placeholder="pacific-freight"
            error={fieldErrors.supplier_id}
            onChange={setSupplierId}
          />
        ) : (
          <>
            <Field label="SKU" value={sku} placeholder="ABC-42" error={fieldErrors.sku} onChange={setSku} />
            <Field
              label="Region"
              value={region}
              placeholder="mx-central"
              error={fieldErrors.region}
              onChange={setRegion}
            />
            <Field
              label="Service level"
              value={serviceLevel}
              placeholder="0.90"
              error={fieldErrors.service_level}
              helper="≥ 0.90 uses P90 demand."
              onChange={setServiceLevel}
            />
          </>
        )}
        <p className="text-[13px] text-muted">
          Workflows today {todayCount} / {workflowCeilings.workflowsPerDay}
        </p>
        <button
          type="submit"
          disabled={limitReached || mutation.isPending}
          title={limitReached ? "Daily workflow limit reached. Resets at 00:00 UTC." : undefined}
          className="h-9 rounded-md bg-accent px-4 text-accent-fg disabled:opacity-60"
        >
          {mutation.isPending ? "Run workflow" : "Run workflow"}
        </button>
      </form>
      {toast ? (
        <p role="status" className="mt-3 text-sm">
          {toast}
        </p>
      ) : null}
      <section className="mt-8">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">Recent workflows</h2>
          <Link href="/workflows" className="text-accent">
            View workflows
          </Link>
        </div>
        <WorkflowTable runs={(workflows.data?.workflow_runs ?? []).slice(0, 5)} />
      </section>
    </div>
  );
}

function KindCard({
  title,
  body,
  selected,
  onSelect,
}: {
  title: string;
  body: string;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className={`rounded-lg border p-4 text-left ${selected ? "border-accent bg-selected" : "border-line bg-surface"}`}
    >
      <span className="block font-semibold">{title}</span>
      <span className="mt-1 block text-[13px] text-muted">{body}</span>
    </button>
  );
}

function Field({
  label,
  value,
  placeholder,
  error,
  helper,
  onChange,
}: {
  label: string;
  value: string;
  placeholder: string;
  error?: string;
  helper?: string;
  onChange: (value: string) => void;
}) {
  const id = label.toLowerCase().replaceAll(" ", "-");
  return (
    <div>
      <label htmlFor={id} className="block text-[13px] font-medium">
        {label}
      </label>
      <input
        id={id}
        value={value}
        placeholder={placeholder}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `${id}-error` : undefined}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 h-9 w-full rounded-md border border-line-strong bg-surface px-2"
      />
      {helper ? <p className="mt-1 text-[12px] text-subtle">{helper}</p> : null}
      {error ? (
        <p id={`${id}-error`} className="mt-1 text-[13px] text-[var(--danger-fg)]">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function WorkflowTable({
  runs,
}: {
  runs: {
    id: string;
    status: string;
    workflow_type: string;
    requested_by: string;
    started_at: string;
    finished_at: string | null;
    input: Record<string, unknown>;
    proposals: { arguments: Record<string, unknown> }[];
    tasks: { agent_type: string; results: { facts: Record<string, unknown> }[] }[];
    cost_summary: { model_calls: number; estimated_cost_cents: number | null };
  }[];
}) {
  if (runs.length === 0) return null;
  return (
    <div className="overflow-auto rounded-lg border border-line">
      <table className="w-full border-collapse text-left text-[13px]">
        <thead className="bg-sunken text-muted">
          <tr>
            {["Status", "Workflow", "Outcome", "Requested by", "Started"].map((heading) => (
              <th key={heading} scope="col" className="px-3 py-2 font-medium">
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const status = workflowStatusLabels[run.status] ?? { label: run.status, tone: "neutral" };
            return (
              <tr key={run.id} className="border-t border-line">
                <td className="px-3 py-2">
                  <StatusBadge status={run.status} label={status.label} tone={status.tone} />
                </td>
                <td className="px-3 py-2">
                  <Link href={`/workflows/${run.id}`} className="text-accent">
                    {workflowTitle(run as never)}
                  </Link>
                </td>
                <td className="px-3 py-2 tabular">{workflowOutcome(run as never)}</td>
                <td className="px-3 py-2">{run.requested_by}</td>
                <td className="px-3 py-2">{formatRelative(run.started_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
