"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { FactGrid } from "@/components/fact-card";
import { ProposalSummary } from "@/components/proposal-summary";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { ApiError, decideApproval, getApproval, getWorkflow, listApprovals } from "@/lib/api";
import {
  formatAbsoluteUtc,
  formatMoneyCents,
  formatRelative,
  formatUnits,
  isUuid,
  truncateMiddle,
} from "@/lib/format";
import { approvalRoleLabels, approvalStatusLabels } from "@/lib/labels";
import { useSession } from "@/lib/session";
import type { ApprovalDetail, ApprovalItem } from "@/lib/types";

const views = [
  { id: "mine", label: "Needs my decision" },
  { id: "pending", label: "All pending" },
  { id: "decided", label: "Decided" },
] as const;

type ViewId = (typeof views)[number]["id"];

const roleOrder = ["procurement_manager", "finance_approver"];

export function ApprovalList() {
  const { tenantId, role } = useSession();
  const params = useSearchParams();
  const router = useRouter();
  const requested = params.get("view");
  const view: ViewId = requested === "pending" || requested === "decided" ? requested : "mine";
  const query = useQuery({
    queryKey: ["approvals", tenantId],
    queryFn: () => listApprovals(tenantId),
  });

  if (query.isLoading) return <LoadingRows />;
  if (query.error) {
    return (
      <ErrorState
        title="Couldn't load approvals"
        message={query.error.message}
        onRetry={() => void query.refetch()}
      />
    );
  }

  const rows = filterApprovals(query.data?.approvals ?? [], view, role);
  return (
    <div>
      <h1 className="text-xl font-semibold">Approvals</h1>
      <div className="mt-4 flex flex-wrap gap-2" role="tablist">
        {views.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={view === item.id}
            className={`h-8 rounded-md px-3 ${view === item.id ? "bg-selected" : "text-muted"}`}
            onClick={() => {
              const next = new URLSearchParams(params.toString());
              if (item.id === "mine") next.delete("view");
              else next.set("view", item.id);
              const queryString = next.toString();
              router.replace(queryString ? `/approvals?${queryString}` : "/approvals");
            }}
          >
            {item.label}
          </button>
        ))}
      </div>
      {rows.length === 0 ? (
        <div className="mt-4">
          <EmptyState
            title={emptyTitle(view)}
            body="Pending approvals appear here when a proposal requires a decision."
            actionLabel="View workflows"
            actionHref="/workflows"
          />
        </div>
      ) : (
        <div className="mt-4 overflow-auto rounded-lg border border-line">
          <table className="w-full border-collapse text-left text-[13px]">
            <thead className="bg-sunken text-muted">
              <tr>
                {["Proposal", "Amount", "Required role", "Expires", "Requested", "Workflow"].map(
                  (heading) => (
                    <th key={heading} scope="col" className="px-3 py-2 font-medium">
                      {heading}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {rows.map((approval) => (
                <tr key={approval.id} className="border-t border-line">
                  <td className="px-3 py-2">
                    <Link href={`/approvals/${approval.id}`} className="text-accent">
                      {proposalLabel(approval)}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-right tabular">
                    {formatMoneyCents(approval.extended_cost_cents)}
                  </td>
                  <td className="px-3 py-2">
                    {approvalRoleLabels[approval.required_role] ?? approval.required_role}
                  </td>
                  <td
                    className={`px-3 py-2 ${expiresSoon(approval.expires_at) ? "text-[var(--warning-fg)]" : ""}`}
                  >
                    <time dateTime={approval.expires_at} title={formatAbsoluteUtc(approval.expires_at)}>
                      {formatRelative(approval.expires_at)}
                    </time>
                  </td>
                  <td className="px-3 py-2">
                    <time dateTime={approval.requested_at} title={formatAbsoluteUtc(approval.requested_at)}>
                      {formatRelative(approval.requested_at)}
                    </time>
                  </td>
                  <td className="px-3 py-2">
                    <Link href={`/workflows/${approval.workflow_run_id}`} className="text-accent">
                      Open workflow
                    </Link>
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

export function ApprovalReview({ approvalId }: { approvalId: string }) {
  const { tenantId, tenantName, role } = useSession();
  const queryClient = useQueryClient();
  const valid = isUuid(approvalId);
  const approvalQuery = useQuery({
    queryKey: ["approval", tenantId, approvalId],
    enabled: valid,
    queryFn: () => getApproval(tenantId, approvalId),
  });
  const runId = approvalQuery.data?.workflow_run_id;
  const workflowQuery = useQuery({
    queryKey: ["workflow", tenantId, runId],
    enabled: Boolean(runId),
    queryFn: () => getWorkflow(tenantId, runId ?? ""),
  });
  const [comment, setComment] = useState("");
  const [rejectError, setRejectError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState("");
  const [banner, setBanner] = useState("");

  useEffect(() => {
    if (!confirming) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setConfirming(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [confirming]);

  const mutation = useMutation({
    mutationFn: (body: { decision: "approve" | "reject"; comment?: string }) =>
      decideApproval(tenantId, approvalId, body),
    onSuccess: async (detail) => {
      setNotice(detail.status === "APPROVED" ? "Approval recorded." : "Rejection recorded.");
      setBanner("");
      setConfirming(false);
      queryClient.setQueryData(["approval", tenantId, approvalId], detail);
      await queryClient.invalidateQueries({ queryKey: ["approvals", tenantId] });
      await queryClient.invalidateQueries({
        queryKey: ["workflow", tenantId, detail.workflow_run_id],
      });
    },
    onError: (error: Error) => {
      setConfirming(false);
      if (error instanceof ApiError && error.status === 409) {
        setBanner(error.message);
        void approvalQuery.refetch();
        return;
      }
      setBanner(error.message);
    },
  });

  if (!valid) return <MissingApproval tenantName={tenantName} />;
  if (approvalQuery.isLoading) return <LoadingRows />;
  if (approvalQuery.error instanceof ApiError && approvalQuery.error.status === 404) {
    return <MissingApproval tenantName={tenantName} />;
  }
  if (approvalQuery.error || !approvalQuery.data) {
    return (
      <ErrorState
        title="Couldn't load this approval"
        message={approvalQuery.error?.message ?? "The approval could not be loaded."}
        onRetry={() => void approvalQuery.refetch()}
      />
    );
  }

  const approval = approvalQuery.data;
  const proposal = workflowQuery.data?.proposals.find((item) => item.id === approval.proposal_id);
  const stale = approval.arguments_hash !== approval.live_arguments_hash;
  const expired = isExpired(approval);
  const canDecide = approval.status === "PENDING" && !expired && !stale && approval.required_role === role;
  const reason = decisionReason(approval, role, stale, expired);
  const status = approvalStatusLabels[approval.status] ?? { label: approval.status, tone: "neutral" };

  return (
    <div>
      <p className="text-[12px] text-muted">
        <Link href="/approvals" className="text-accent">
          Approvals
        </Link>
        <span> / </span>
        <span className="font-mono" title={approval.id}>
          {truncateMiddle(approval.id, 8, 4)}
        </span>
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">{proposalLabel(approval)}</h1>
        <StatusBadge status={approval.status} label={status.label} tone={status.tone} />
      </div>
      {banner ? (
        <p
          data-testid="approval-conflict"
          role="alert"
          className="mt-4 rounded-lg border border-[var(--danger-fg)] bg-[var(--danger-bg)] px-4 py-3 text-[var(--danger-fg)]"
        >
          {banner}
        </p>
      ) : null}
      {stale ? (
        <p
          data-testid="stale-hash"
          role="alert"
          className="mt-4 rounded-lg border border-[var(--danger-fg)] bg-[var(--danger-bg)] px-4 py-3 text-[var(--danger-fg)]"
        >
          Proposal changed after this approval was requested. A new approval is required.
        </p>
      ) : null}
      <div className="mt-6 grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="space-y-4">
          {proposal ? <ProposalSummary proposal={proposal} /> : <LiveSummary approval={approval} />}
          {workflowQuery.data ? <FactGrid tasks={workflowQuery.data.tasks} /> : null}
          {workflowQuery.data ? <EvidenceTable evidence={workflowQuery.data.evidence} /> : null}
        </div>
        <aside className="rounded-lg border border-line bg-surface p-4 xl:sticky xl:top-6">
          <p className="text-sm font-semibold">
            {expired ? "Expired approval" : status.label}
            {!expired && approval.status === "PENDING" ? (
              <span className="font-normal text-muted">
                {" "}
                · expires{" "}
                <time dateTime={approval.expires_at} title={formatAbsoluteUtc(approval.expires_at)}>
                  {formatRelative(approval.expires_at)}
                </time>
              </span>
            ) : null}
          </p>
          {expired ? (
            <p className="mt-3 text-sm">
              This approval expired on {formatAbsoluteUtc(approval.expires_at)}.
            </p>
          ) : null}
          <h2 className="mt-4 text-sm font-semibold">Required approvals</h2>
          <ul className="mt-2 space-y-2 text-sm">
            {orderedApprovals(approval).map((item) => {
              const itemStatus = approvalStatusLabels[item.status] ?? {
                label: item.status,
                tone: "neutral",
              };
              const mine = item.required_role === role;
              return (
                <li key={item.id} className="flex items-center justify-between gap-2">
                  <span>
                    {approvalRoleLabels[item.required_role] ?? item.required_role}
                    {mine ? " (you)" : ""}
                  </span>
                  <StatusBadge
                    status={item.status}
                    label={itemStatus.label}
                    tone={itemStatus.tone}
                    testId={`approval-step-${item.required_role}`}
                  />
                </li>
              );
            })}
            {approval.proposal_approvals.some((item) => item.required_role === "finance_approver") ? null : (
              <li className="text-muted">Finance approver — not required</li>
            )}
          </ul>
          <div className="mt-4">
            <p className="text-[12px] text-muted">Arguments hash</p>
            <p className="mt-1 font-mono text-[13px]" data-testid="approval-hash" title={approval.live_arguments_hash}>
              {truncateMiddle(approval.live_arguments_hash)}
            </p>
            {stale ? (
              <p className="mt-2 font-mono text-[13px] text-[var(--danger-fg)]" title={approval.arguments_hash}>
                Approval hash {truncateMiddle(approval.arguments_hash)}
              </p>
            ) : null}
          </div>
          {approval.approved_by ? (
            <p className="mt-3 text-[13px] text-muted">Decided by {approval.approved_by}</p>
          ) : null}
          {notice ? (
            <p className="mt-3 text-sm" aria-live="polite">
              {notice}
            </p>
          ) : null}
          {approval.status === "PENDING" && !expired ? (
            <div className="mt-4 space-y-3">
              <label className="block text-sm">
                Comment
                <textarea
                  data-testid="reject-comment"
                  className="mt-1 w-full rounded-md border border-line bg-surface px-2 py-1"
                  rows={3}
                  value={comment}
                  onChange={(event) => {
                    setComment(event.target.value);
                    setRejectError("");
                  }}
                />
              </label>
              {rejectError ? (
                <p data-testid="reject-error" className="text-sm text-[var(--danger-fg)]">
                  {rejectError}
                </p>
              ) : null}
              {reason ? (
                <p data-testid="decision-reason" className="text-[13px] text-muted">
                  {reason}
                </p>
              ) : null}
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  data-testid="reject-proposal"
                  title={reason || undefined}
                  disabled={!canDecide || mutation.isPending}
                  className="h-8 rounded-md border border-[var(--danger-fg)] px-3 text-[var(--danger-fg)] disabled:opacity-50"
                  onClick={() => {
                    if (!comment.trim()) {
                      setRejectError("A comment is required to reject.");
                      return;
                    }
                    setRejectError("");
                    mutation.mutate({ decision: "reject", comment: comment.trim() });
                  }}
                >
                  Reject
                </button>
                <button
                  type="button"
                  data-testid="approve-proposal"
                  title={reason || undefined}
                  disabled={!canDecide || mutation.isPending}
                  className="h-8 rounded-md bg-accent px-3 text-accent-fg disabled:opacity-50"
                  onClick={() => setConfirming(true)}
                >
                  Approve
                </button>
              </div>
            </div>
          ) : null}
        </aside>
      </div>
      {confirming ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="approve-title"
          data-testid="approve-dialog"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
        >
          <div className="w-full max-w-lg rounded-xl border border-line bg-raised p-5">
            <h2 id="approve-title" className="text-base font-semibold">
              {confirmText(approval)}
            </h2>
            <p className="mt-2 font-mono text-[13px] text-muted" title={approval.live_arguments_hash}>
              This approval is bound to hash {truncateMiddle(approval.live_arguments_hash)}.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                className="h-8 rounded-md border border-line px-3"
                onClick={() => setConfirming(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid="confirm-approve"
                disabled={mutation.isPending}
                className="h-8 rounded-md bg-accent px-3 text-accent-fg disabled:opacity-50"
                onClick={() => mutation.mutate({ decision: "approve" })}
              >
                {approval.quantity === null ? "Approve proposal" : `Approve ${approval.quantity} units`}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function LiveSummary({ approval }: { approval: ApprovalItem }) {
  return (
    <section className="rounded-lg border border-line bg-surface p-4">
      <h2 className="text-base font-semibold">Deterministic result</h2>
      <p className="mt-3 tabular text-[28px] leading-9 font-semibold">
        {approval.quantity === null ? "—" : formatUnits(approval.quantity)}
      </p>
      <p className="tabular text-sm">{formatMoneyCents(approval.extended_cost_cents)}</p>
    </section>
  );
}

function EvidenceTable({
  evidence,
}: {
  evidence: { id: string; type: string; source: string; source_record_id: string; observed_at: string }[];
}) {
  if (evidence.length === 0) return null;
  return (
    <div className="overflow-auto rounded-lg border border-line">
      <table className="w-full border-collapse text-left text-[13px]">
        <thead className="bg-sunken text-muted">
          <tr>
            {["Type", "Source", "Record", "Observed"].map((heading) => (
              <th key={heading} scope="col" className="px-3 py-2 font-medium">
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {evidence.map((item) => (
            <tr key={item.id} className="border-t border-line">
              <td className="px-3 py-2">{item.type}</td>
              <td className="px-3 py-2">{item.source}</td>
              <td className="px-3 py-2 font-mono">{item.source_record_id}</td>
              <td className="px-3 py-2">
                <time dateTime={item.observed_at} title={formatAbsoluteUtc(item.observed_at)}>
                  {formatRelative(item.observed_at)}
                </time>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MissingApproval({ tenantName }: { tenantName: string }) {
  return (
    <div data-testid="not-found" className="rounded-lg border border-line bg-surface px-6 py-10">
      <h1 className="text-xl font-semibold">Approval not found</h1>
      <p className="mt-2 max-w-xl text-muted">
        It doesn&apos;t exist in tenant {tenantName}, or you don&apos;t have access.
      </p>
      <Link href="/approvals" className="mt-4 inline-flex text-accent">
        Back to approvals
      </Link>
    </div>
  );
}

function filterApprovals(approvals: ApprovalItem[], view: ViewId, role: string): ApprovalItem[] {
  if (view === "decided") {
    return approvals.filter((item) => item.status !== "PENDING");
  }
  const pending = approvals.filter((item) => item.status === "PENDING");
  if (view === "pending") return pending;
  return pending.filter((item) => item.required_role === role);
}

function emptyTitle(view: ViewId): string {
  if (view === "decided") return "No decided approvals";
  if (view === "pending") return "No pending approvals";
  return "No approvals need your decision";
}

function proposalLabel(approval: ApprovalItem): string {
  const quantity = approval.quantity === null ? "Reorder" : `Reorder ${approval.quantity} units`;
  return approval.sku ? `${quantity} · ${approval.sku}` : quantity;
}

function expiresSoon(iso: string): boolean {
  const delta = new Date(iso).getTime() - Date.now();
  return delta > 0 && delta < 24 * 60 * 60 * 1000;
}

function isExpired(approval: ApprovalItem): boolean {
  return approval.status === "EXPIRED" || new Date(approval.expires_at).getTime() <= Date.now();
}

function decisionReason(
  approval: ApprovalItem,
  role: string,
  stale: boolean,
  expired: boolean,
): string {
  if (expired || approval.status !== "PENDING") return "";
  if (stale) return "The proposal arguments changed.";
  if (approval.required_role !== role) return `Requires ${approval.required_role} role.`;
  return "";
}

function orderedApprovals(approval: ApprovalDetail): ApprovalItem[] {
  return [...approval.proposal_approvals].sort((left, right) => {
    const leftRank = roleOrder.indexOf(left.required_role);
    const rightRank = roleOrder.indexOf(right.required_role);
    return (leftRank === -1 ? 9 : leftRank) - (rightRank === -1 ? 9 : rightRank);
  });
}

function confirmText(approval: ApprovalItem): string {
  const quantity = approval.quantity === null ? "this reorder" : `${approval.quantity} units`;
  const sku = approval.sku ? ` of ${approval.sku}` : "";
  const supplier = approval.supplier_id ? ` from ${approval.supplier_id}` : "";
  const money =
    approval.extended_cost_cents === null ? "" : ` for ${formatMoneyCents(approval.extended_cost_cents)}`;
  return `Approve reorder of ${quantity}${sku}${supplier}${money}?`;
}
