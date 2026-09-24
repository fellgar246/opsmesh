"use client";

import { Boxes, Scale, TrendingUp, Truck, Wallet } from "lucide-react";
import type { ComponentType } from "react";

import { formatAbsoluteUtc, formatMoneyCents, formatRelative, formatUnits, readNumber } from "@/lib/format";
import { agentLabels } from "@/lib/labels";
import type { AgentTask } from "@/lib/types";

const icons: Record<string, ComponentType<{ size?: number }>> = {
  inventory: Boxes,
  demand: TrendingUp,
  supplier: Truck,
  finance: Wallet,
  policy: Scale,
};

const colors: Record<string, string> = {
  inventory: "var(--agent-inventory)",
  demand: "var(--agent-demand)",
  supplier: "var(--agent-supplier)",
  finance: "var(--agent-finance)",
  policy: "var(--agent-policy)",
};

function recordsOf(task: AgentTask): Record<string, unknown>[] {
  const facts = task.results[0]?.facts;
  if (!facts) return [];
  const records = facts.records;
  if (Array.isArray(records)) {
    return records.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null);
  }
  if (Object.keys(facts).length === 0) return [];
  return [facts];
}

function headline(agent: string, record: Record<string, unknown> | undefined): { label: string; value: string } {
  if (!record) return { label: "Records", value: "—" };
  if (agent === "inventory") {
    const available = readNumber(record.available_units);
    return { label: "Available units", value: available === null ? "—" : formatUnits(available) };
  }
  if (agent === "demand") {
    const p50 = readNumber(record.p50);
    const p90 = readNumber(record.p90);
    return {
      label: "Forecast",
      value: p50 === null || p90 === null ? "—" : `P50 ${p50} · P90 ${p90}`,
    };
  }
  if (agent === "supplier") {
    const cost = readNumber(record.unit_cost_cents);
    return { label: "Unit cost", value: formatMoneyCents(cost) };
  }
  if (agent === "finance") {
    const available = readNumber(record.available_cents);
    return { label: "Available budget", value: formatMoneyCents(available) };
  }
  const reason = typeof record.reason === "string" ? record.reason : null;
  return { label: "Policy", value: reason ?? "Recorded" };
}

export function FactGrid({ tasks }: { tasks: AgentTask[] }) {
  const order = ["inventory", "demand", "supplier", "finance", "policy"];
  const sorted = [...tasks].sort(
    (left, right) => order.indexOf(left.agent_type) - order.indexOf(right.agent_type),
  );
  if (sorted.length === 0) {
    return <p className="text-muted">No specialist output was recorded.</p>;
  }
  return (
    <div className="grid gap-3 xl:grid-cols-2">
      {sorted.map((task) => {
        const Icon = icons[task.agent_type] ?? Boxes;
        const record = recordsOf(task)[0];
        const fact = headline(task.agent_type, record);
        const evidence = task.results[0]?.evidence[0];
        const confidence = task.results[0]?.confidence;
        return (
          <article key={task.id} className="rounded-lg border border-line bg-surface p-4">
            <header className="flex items-center justify-between gap-2">
              <h3 className="flex items-center gap-2 text-sm font-semibold">
                <span style={{ color: colors[task.agent_type] }}>
                  <Icon size={16} aria-hidden />
                </span>
                {agentLabels[task.agent_type] ?? task.agent_type}
              </h3>
              {confidence ? <span className="text-[12px] text-muted">{confidence} confidence</span> : null}
            </header>
            <p className="mt-3 text-[12px] text-muted">{fact.label}</p>
            <p className="tabular text-[28px] leading-9 font-semibold">{fact.value}</p>
            {evidence ? (
              <p className="mt-3 border-t border-line pt-3 text-[12px] text-muted">
                {evidence.source} · {evidence.source_record_id} · observed{" "}
                <time dateTime={evidence.observed_at} title={formatAbsoluteUtc(evidence.observed_at)}>
                  {formatRelative(evidence.observed_at)}
                </time>
              </p>
            ) : (
              <p className="mt-3 text-[12px] text-muted">missing_evidence</p>
            )}
          </article>
        );
      })}
    </div>
  );
}
