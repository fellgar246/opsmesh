"use client";

import { use } from "react";

import { ApprovalReview } from "@/components/approvals";

export default function ApprovalPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <ApprovalReview approvalId={id} />;
}
