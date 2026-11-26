"use client";

import { Check, ChevronDown, Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";

import { localIdentity } from "@/lib/identity";
import { useSession } from "@/lib/session";

const operate = [
  { href: "/operations", label: "Operations" },
  { href: "/workflows", label: "Workflows" },
  { href: "/proposals", label: "Proposals" },
  { href: "/approvals", label: "Approvals" },
];

const observe = [
  { href: "/agents", label: "Agents" },
  { href: "/evaluations", label: "Evaluations" },
  { href: "/cost", label: "Cost" },
];

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto px-3 py-4">
      <Section title="Operate" items={operate} pathname={pathname} onNavigate={onNavigate} />
      <Section title="Observe" items={observe} pathname={pathname} onNavigate={onNavigate} />
    </div>
  );
}

function Section({
  title,
  items,
  pathname,
  onNavigate,
}: {
  title: string;
  items: { href: string; label: string }[];
  pathname: string;
  onNavigate?: () => void;
}) {
  return (
    <div>
      <p className="px-2 text-[12px] font-medium tracking-[0.04em] text-subtle uppercase">
        {title}
      </p>
      <ul className="mt-2 space-y-1">
        {items.map((item) => {
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                onClick={onNavigate}
                aria-current={active ? "page" : undefined}
                className={`flex h-8 items-center rounded-md border-l-2 px-2 ${
                  active
                    ? "border-accent bg-selected text-ink"
                    : "border-transparent text-muted hover:bg-sunken"
                }`}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { tenantId, tenantName, tenants, setTenantId } = useSession();
  const [open, setOpen] = useState(false);
  const [menu, setMenu] = useState(false);

  return (
    <div className="min-h-screen bg-canvas text-ink">
      <a
        href="#content"
        className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:m-2 focus:bg-surface focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <div className="flex min-h-screen">
        <aside className="hidden w-60 shrink-0 flex-col border-r border-line bg-surface lg:flex">
          <SidebarBody
            tenantName={tenantName}
            tenantId={tenantId}
            tenants={tenants}
            menu={menu}
            setMenu={setMenu}
            setTenantId={setTenantId}
          />
        </aside>
        {open ? (
          <div className="fixed inset-0 z-40 lg:hidden">
            <button
              type="button"
              aria-label="Close navigation"
              className="absolute inset-0 bg-black/40"
              onClick={() => setOpen(false)}
            />
            <aside className="relative flex h-full w-60 flex-col bg-surface">
              <SidebarBody
                tenantName={tenantName}
                tenantId={tenantId}
                tenants={tenants}
                menu={menu}
                setMenu={setMenu}
                setTenantId={setTenantId}
                onNavigate={() => setOpen(false)}
              />
            </aside>
          </div>
        ) : null}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-3 border-b border-line px-4 py-3 lg:hidden">
            <button
              type="button"
              aria-label="Open navigation"
              className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line"
              onClick={() => setOpen(true)}
            >
              <Menu size={16} />
            </button>
            <p className="text-sm font-medium">{tenantName}</p>
          </div>
          <main id="content" className="px-4 py-6 xl:px-6">
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}

function SidebarBody({
  tenantName,
  tenantId,
  tenants,
  menu,
  setMenu,
  setTenantId,
  onNavigate,
}: {
  tenantName: string;
  tenantId: string;
  tenants: { tenant_id: string; name: string }[];
  menu: boolean;
  setMenu: (open: boolean) => void;
  setTenantId: (tenantId: string) => void;
  onNavigate?: () => void;
}) {
  return (
    <>
      <div className="relative border-b border-line p-3">
        <button
          type="button"
          aria-expanded={menu}
          aria-haspopup="listbox"
          className="flex h-10 w-full items-center justify-between rounded-md border border-line px-2 text-left"
          onClick={() => setMenu(!menu)}
        >
          <span>
            <span className="block text-[12px] text-subtle">Tenant</span>
            <span className="block font-medium" data-testid="active-tenant">
              {tenantName}
            </span>
          </span>
          <ChevronDown size={16} />
        </button>
        {menu ? (
          <ul
            role="listbox"
            className="absolute right-3 left-3 z-20 mt-1 rounded-lg border border-line bg-raised p-1 shadow-lg"
          >
            {tenants.map((tenant) => (
              <li key={tenant.tenant_id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={tenant.tenant_id === tenantId}
                  className="flex h-8 w-full items-center justify-between rounded px-2 text-left hover:bg-sunken"
                  onClick={() => {
                    setMenu(false);
                    if (tenant.tenant_id !== tenantId) setTenantId(tenant.tenant_id);
                  }}
                >
                  {tenant.name}
                  {tenant.tenant_id === tenantId ? <Check size={14} /> : null}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <nav aria-label="Primary">
        <NavLinks onNavigate={onNavigate} />
      </nav>
      <div className="mt-auto border-t border-line p-3 text-[12px] text-muted">
        <p className="inline-flex rounded bg-[var(--neutral-bg)] px-1.5 py-0.5 text-[var(--neutral-fg)]">
          Local simulation
        </p>
        <p className="mt-2">
          {localIdentity.displayName}
          <span className="text-subtle"> · {localIdentity.roleLabel}</span>
        </p>
      </div>
      {onNavigate ? (
        <button
          type="button"
          aria-label="Close navigation"
          className="absolute top-3 right-3 lg:hidden"
          onClick={onNavigate}
        >
          <X size={16} />
        </button>
      ) : null}
    </>
  );
}
