"use client";

import { InactiveState } from "@/components/states";

export default function AgentsPage() {
  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Agents</h1>
      <InactiveState
        title="Agent metrics are not available yet"
        body="Specialist agents are not enabled in this environment. Metrics appear after the first agent-backed run."
      />
    </div>
  );
}
