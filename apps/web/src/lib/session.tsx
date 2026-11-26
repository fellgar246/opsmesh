"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { listTenants } from "./api";
import type { Tenant } from "./types";

const storageKey = "opsmesh.tenant";

type SessionValue = {
  tenantId: string;
  tenantName: string;
  tenants: Tenant[];
  setTenantId: (tenantId: string) => void;
};

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [tenantId, setTenantIdState] = useState("northwind");
  const [tenants, setTenants] = useState<Tenant[]>([
    { tenant_id: "northwind", name: "Northwind" },
    { tenant_id: "contoso", name: "Contoso" },
  ]);

  useEffect(() => {
    const stored = window.localStorage.getItem(storageKey);
    if (stored) setTenantIdState(stored);
  }, []);

  useEffect(() => {
    let cancelled = false;
    listTenants()
      .then((body) => {
        if (!cancelled && body.tenants.length > 0) setTenants(body.tenants);
      })
      .catch(() => {
        /* Keep the configured local tenants when the API is unreachable. */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const setTenantId = useCallback(
    (next: string) => {
      setTenantIdState(next);
      window.localStorage.setItem(storageKey, next);
      queryClient.clear();
      router.push("/operations");
    },
    [queryClient, router],
  );

  const tenantName = tenants.find((tenant) => tenant.tenant_id === tenantId)?.name ?? tenantId;
  const value = useMemo(
    () => ({ tenantId, tenantName, tenants, setTenantId }),
    [tenantId, tenantName, tenants, setTenantId],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("Session is unavailable");
  return value;
}
