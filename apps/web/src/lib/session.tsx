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

import { listTenants, openLocalSession, setAccessToken } from "./api";
import { isLocalRole, localIdentities, type LocalRole } from "./identity";
import type { Tenant } from "./types";

const storageKey = "opsmesh.tenant";
const roleKey = "opsmesh.role";

type SessionValue = {
  tenantId: string;
  tenantName: string;
  tenants: Tenant[];
  setTenantId: (tenantId: string) => void;
  role: LocalRole;
  actor: string;
  roleLabel: string;
  setRole: (role: LocalRole) => void;
};

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [tenantId, setTenantIdState] = useState("northwind");
  const [role, setRoleState] = useState<LocalRole>("operations_analyst");
  const [tenants, setTenants] = useState<Tenant[]>([
    { tenant_id: "northwind", name: "Northwind" },
    { tenant_id: "contoso", name: "Contoso" },
  ]);
  const [hydrated, setHydrated] = useState(false);
  const [accessToken, setToken] = useState<string | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);

  useEffect(() => {
    const stored = window.localStorage.getItem(storageKey);
    if (stored) setTenantIdState(stored);
    const storedRole = window.localStorage.getItem(roleKey);
    if (storedRole && isLocalRole(storedRole)) setRoleState(storedRole);
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    let cancelled = false;
    setAccessToken("");
    setToken(null);
    setSessionError(null);
    openLocalSession(tenantId, role)
      .then((session) => {
        if (cancelled) return;
        setAccessToken(session.access_token);
        setToken(session.access_token);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setSessionError(error instanceof Error ? error.message : "Sign-in failed.");
      });
    return () => {
      cancelled = true;
    };
  }, [hydrated, tenantId, role]);

  useEffect(() => {
    if (!accessToken) return;
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
  }, [accessToken]);

  const setTenantId = useCallback(
    (next: string) => {
      setTenantIdState(next);
      window.localStorage.setItem(storageKey, next);
      queryClient.clear();
      router.push("/operations");
    },
    [queryClient, router],
  );

  const setRole = useCallback((next: LocalRole) => {
    setRoleState(next);
    window.localStorage.setItem(roleKey, next);
  }, []);

  const tenantName = tenants.find((tenant) => tenant.tenant_id === tenantId)?.name ?? tenantId;
  const identity = localIdentities[role];
  const value = useMemo(
    () => ({
      tenantId,
      tenantName,
      tenants,
      setTenantId,
      role,
      actor: identity.actor,
      roleLabel: identity.label,
      setRole,
    }),
    [tenantId, tenantName, tenants, setTenantId, role, identity.actor, identity.label, setRole],
  );

  if (sessionError) {
    return <p className="p-6 text-sm text-muted">{sessionError}</p>;
  }
  if (!accessToken) {
    return <p className="p-6 text-sm text-muted">Signing in…</p>;
  }
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("Session is unavailable");
  return value;
}
