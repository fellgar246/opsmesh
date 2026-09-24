"use client";

import {
  Ban,
  Check,
  CheckCircle2,
  CircleDashed,
  CircleDot,
  Hourglass,
  LoaderCircle,
  SkipForward,
  X,
  XCircle,
} from "lucide-react";
import type { ComponentType } from "react";

const icons: Record<string, ComponentType<{ size?: number; className?: string }>> = {
  PENDING: CircleDashed,
  READY: CircleDot,
  RUNNING: LoaderCircle,
  WAITING_APPROVAL: Hourglass,
  COMPLETED: CheckCircle2,
  FAILED: XCircle,
  CANCELLED: Ban,
  SKIPPED: SkipForward,
  APPROVED: Check,
  REJECTED: X,
  EXPIRED: Hourglass,
  DRAFT: CircleDashed,
  PENDING_APPROVAL: Hourglass,
  SUPERSEDED: Ban,
};

const tones: Record<string, string> = {
  neutral: "bg-[var(--neutral-bg)] text-[var(--neutral-fg)]",
  info: "bg-[var(--info-bg)] text-[var(--info-fg)]",
  success: "bg-[var(--success-bg)] text-[var(--success-fg)]",
  warning: "bg-[var(--warning-bg)] text-[var(--warning-fg)]",
  danger: "bg-[var(--danger-bg)] text-[var(--danger-fg)]",
  expired: "bg-[var(--expired-bg)] text-[var(--expired-fg)]",
};

export function StatusBadge({
  status,
  label,
  tone,
  testId = "status-badge",
}: {
  status: string;
  label: string;
  tone: string;
  testId?: string;
}) {
  const Icon = icons[status] ?? CircleDashed;
  const spinning = status === "RUNNING";
  return (
    <span
      data-testid={testId}
      className={`inline-flex h-5 items-center gap-1 rounded px-1.5 text-[12px] font-medium leading-4 ${tones[tone] ?? tones.neutral}`}
    >
      <Icon
        size={12}
        aria-hidden
        className={spinning ? "motion-safe:animate-spin" : undefined}
      />
      {label}
    </span>
  );
}
