"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { EmptyState, ErrorState, LoadingRows } from "@/components/states";
import { listAuditEvents } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { eventTypeLabels } from "@/lib/labels";
import { useSession } from "@/lib/session";
import type { AuditEvent } from "@/lib/types";

const summaryKeys = ["agent_type", "reason", "status", "required_role", "purchase_order_id"];

function summarize(payload: Record<string, unknown>): string {
  const parts = summaryKeys.flatMap((key) => {
    const value = payload[key];
    return typeof value === "string" && value.length > 0 ? [value] : [];
  });
  return parts.join(" · ") || "—";
}

export function AuditList() {
  const { tenantId } = useSession();
  const query = useQuery({
    queryKey: ["audit-events", tenantId],
    queryFn: () => listAuditEvents(tenantId),
  });
  if (query.isLoading) return <LoadingRows />;
  if (query.error) {
    return (
      <ErrorState
        title="Couldn't load the audit"
        message={query.error.message}
        onRetry={() => void query.refetch()}
      />
    );
  }
  const events = query.data?.events ?? [];
  return (
    <div>
      <h1 className="text-xl font-semibold">Audit</h1>
      <p className="mt-1 text-muted">Domain events committed for this tenant.</p>
      {events.length === 0 ? (
        <div className="mt-4">
          <EmptyState
            title="No audit events yet"
            body="Run a reorder recommendation. Each committed change appears here after it is published."
          />
        </div>
      ) : (
        <div className="mt-4 overflow-auto rounded-lg border border-line">
          <table className="w-full border-collapse text-left text-[13px]">
            <thead className="bg-sunken text-muted">
              <tr>
                {["Event", "Detail", "Workflow", "When"].map((heading) => (
                  <th key={heading} scope="col" className="px-3 py-2 font-medium">
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <AuditRow key={event.event_id} event={event} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function AuditRow({ event }: { event: AuditEvent }) {
  return (
    <tr data-testid="audit-event" className="border-t border-line">
      <td className="px-3 py-2">{eventTypeLabels[event.event_type] ?? event.event_type}</td>
      <td className="px-3 py-2 text-muted">{summarize(event.payload)}</td>
      <td className="px-3 py-2">
        <Link href={`/workflows/${event.workflow_id}`} className="text-accent">
          View
        </Link>
      </td>
      <td className="px-3 py-2">{formatRelative(event.occurred_at)}</td>
    </tr>
  );
}
