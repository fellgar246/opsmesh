"use client";

import { ShieldCheck } from "lucide-react";

import { StatusBadge } from "@/components/status-badge";
import { formatMoneyCents, formatUnits, readNumber, readString, truncateMiddle } from "@/lib/format";
import { proposalStatusLabels, reasonLabels, roleLabels } from "@/lib/labels";
import type { Proposal } from "@/lib/types";

export function ProposalSummary({ proposal }: { proposal: Proposal }) {
  const quantity = readNumber(proposal.arguments.quantity);
  const cost = readNumber(proposal.arguments.extended_cost_cents);
  const reason = readString(proposal.arguments.reason);
  const reasonInfo = reason ? reasonLabels[reason] : undefined;
  const status = proposalStatusLabels[proposal.status] ?? {
    label: proposal.status,
    tone: "neutral",
  };
  const hash = proposal.approvals[0]?.arguments_hash;
  const explanation = readString(proposal.arguments.explanation);
  const binding = Array.isArray(proposal.arguments.binding_constraints)
    ? proposal.arguments.binding_constraints.filter((item): item is string => typeof item === "string")
    : [];

  return (
    <section
      data-testid="proposal-summary"
      className="rounded-lg border border-line bg-surface p-4"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <ShieldCheck size={16} aria-hidden />
          Deterministic result
        </h2>
        <StatusBadge status={proposal.status} label={status.label} tone={status.tone} />
      </div>
      <div className="mt-4 grid gap-4 sm:grid-cols-3">
        <div>
          <p className="text-[12px] text-muted">Reorder quantity</p>
          <p data-testid="proposal-quantity" className="tabular text-[28px] leading-9 font-semibold">
            {quantity === null ? "—" : formatUnits(quantity)}
          </p>
        </div>
        <div>
          <p className="text-[12px] text-muted">Extended cost</p>
          <p className="tabular text-[28px] leading-9 font-semibold">{formatMoneyCents(cost)}</p>
        </div>
        <div>
          <p className="text-[12px] text-muted">Approvals</p>
          {proposal.approvals.length === 0 ? (
            <p className="mt-1 text-muted">No approval required</p>
          ) : (
            <ul className="mt-1 space-y-1">
              {proposal.approvals.map((approval) => (
                <li key={approval.id} data-testid="approval-role">
                  {roleLabels[approval.required_role] ?? approval.required_role} approval required
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
      {explanation ? (
        <p className="mt-3 text-sm" data-testid="proposal-explanation">
          {explanation}
        </p>
      ) : null}
      {binding.length > 0 ? (
        <p className="mt-2 text-[13px] text-muted">
          Binding constraints {binding.join(", ")}
        </p>
      ) : null}
      {reasonInfo ? (
        <p className="mt-3 text-muted">
          {reasonInfo.label}
          {reasonInfo.detail ? ` — ${reasonInfo.detail}` : ""}
        </p>
      ) : null}
      {hash ? (
        <p className="mt-3 font-mono text-[13px] text-muted" title={hash}>
          Arguments hash {truncateMiddle(hash)}
        </p>
      ) : null}
    </section>
  );
}

export function Infeasibility({
  reason,
  explanation,
}: {
  reason: string;
  explanation?: string | null;
}) {
  const info = reasonLabels[reason] ?? {
    label: reason,
    tone: "danger",
    detail: "The workflow did not produce a proposal.",
  };
  return (
    <section
      data-testid="infeasibility"
      className="rounded-lg border border-line bg-[var(--danger-bg)] p-4 text-[var(--danger-fg)]"
    >
      <h2 className="text-base font-semibold">{info.label}</h2>
      <p className="mt-1">{info.detail}</p>
      <p className="mt-2 font-mono text-[13px]">{reason}</p>
      {explanation ? (
        <p className="mt-2 text-sm" data-testid="constraint-explanation">
          {explanation}
        </p>
      ) : null}
    </section>
  );
}
