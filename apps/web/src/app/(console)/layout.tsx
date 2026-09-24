"use client";

import { AppShell } from "@/components/shell";
import type { ReactNode } from "react";

export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
