"use client";

import { InactiveState } from "@/components/states";

export default function EvaluationsPage() {
  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Evaluations</h1>
      <InactiveState
        title="Evaluations are not available yet"
        body="Evaluation runs are not enabled in this environment. Results appear after the first recorded evaluation."
      />
    </div>
  );
}
