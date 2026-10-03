"use client";

import { Suspense } from "react";

import { ApprovalList } from "@/components/approvals";
import { LoadingRows } from "@/components/states";

export default function ApprovalsPage() {
  return (
    <Suspense fallback={<LoadingRows />}>
      <ApprovalList />
    </Suspense>
  );
}
