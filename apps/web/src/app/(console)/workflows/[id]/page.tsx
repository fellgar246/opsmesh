"use client";

import { use } from "react";

import { WorkflowDetail } from "@/components/workflow-detail";

export default function WorkflowPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <WorkflowDetail runId={id} />;
}
