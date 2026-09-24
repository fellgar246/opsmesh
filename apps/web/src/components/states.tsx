import Link from "next/link";

export function EmptyState({
  title,
  body,
  actionHref = "/operations",
  actionLabel = "Run workflow",
}: {
  title: string;
  body: string;
  actionHref?: string;
  actionLabel?: string;
}) {
  return (
    <div
      data-testid="empty-state"
      className="rounded-lg border border-line bg-surface px-6 py-10"
    >
      <h2 className="text-base font-semibold">{title}</h2>
      <p className="mt-2 max-w-xl text-muted">{body}</p>
      <Link
        href={actionHref}
        className="mt-4 inline-flex h-8 items-center rounded-md bg-accent px-3 text-accent-fg"
      >
        {actionLabel}
      </Link>
    </div>
  );
}

export function ErrorState({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      data-testid="error-state"
      role="alert"
      className="rounded-lg border border-[var(--danger-fg)] bg-[var(--danger-bg)] px-4 py-3 text-[var(--danger-fg)]"
    >
      <h2 className="text-sm font-semibold">{title}</h2>
      <p className="mt-1">{message}</p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 h-8 rounded-md border border-current px-3"
        >
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function NotFoundState({ tenantName }: { tenantName: string }) {
  return (
    <div data-testid="not-found" className="rounded-lg border border-line bg-surface px-6 py-10">
      <h1 className="text-xl font-semibold">Workflow not found</h1>
      <p className="mt-2 max-w-xl text-muted">
        It doesn&apos;t exist in tenant {tenantName}, or you don&apos;t have access.
      </p>
      <Link href="/workflows" className="mt-4 inline-flex text-accent">
        Back to workflows
      </Link>
    </div>
  );
}

export function InactiveState({ title, body }: { title: string; body: string }) {
  return (
    <div data-testid="inactive-state" className="rounded-lg border border-line bg-surface px-6 py-10">
      <h2 className="text-base font-semibold">{title}</h2>
      <p className="mt-2 max-w-xl text-muted">{body}</p>
      <Link href="/workflows" className="mt-4 inline-flex text-accent">
        View workflows
      </Link>
    </div>
  );
}

export function LoadingRows() {
  return (
    <div data-testid="loading-state" className="space-y-2" aria-busy="true" aria-label="Loading">
      {Array.from({ length: 4 }, (_, index) => (
        <div key={index} className="h-10 animate-pulse rounded bg-sunken" />
      ))}
    </div>
  );
}
