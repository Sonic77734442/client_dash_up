"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "../hooks/useAuth";
import {
  type AppRole,
  destinationForRole,
  isAppRole,
  isPathAllowedForRole,
  isPublicPath,
  safeRelativePath,
} from "../lib/authRedirect";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const { ready, authenticated, role, error, refresh } = useAuth(defaultApiBase);
  const [retrying, setRetrying] = useState(false);

  const currentPath = pathname || "";
  const isPublic = isPublicPath(currentPath);
  const currentRole: AppRole | null = isAppRole(role) ? role : null;
  const roleAllowed = Boolean(currentRole && isPathAllowedForRole(currentRole, currentPath));

  useEffect(() => {
    if (!ready) return;

    if (error) {
      // A transient API failure is not proof that the session is invalid.
      // Keep the requested page and let the user retry instead of sending
      // them to login and making a deploy look like a logout.
      return;
    }

    if (!authenticated && !isPublic) {
      const requestedPath = safeRelativePath(
        `${currentPath}${window.location.search}${window.location.hash}`,
        currentPath || "/",
      );
      router.replace(`/login?next=${encodeURIComponent(requestedPath)}`);
      return;
    }

    if (!authenticated || !currentRole) return;

    if (isPublic) {
      const requestedPath = new URLSearchParams(window.location.search).get("next");
      router.replace(destinationForRole(currentRole, requestedPath));
      return;
    }

    if (!roleAllowed) {
      router.replace(destinationForRole(currentRole));
    }
  }, [ready, authenticated, currentPath, currentRole, error, isPublic, roleAllowed, router]);

  if ((ready && error && !authenticated && !isPublic) || (retrying && !isPublic)) {
    return (
      <main className="auth-outage-shell" role="status" aria-live="polite">
        <section className="auth-outage-card">
          <div className="auth-outage-mark" aria-hidden="true">↻</div>
          <div className="auth-outage-eyebrow">Соединение с платформой</div>
          <h1>Не удалось связаться с платформой</h1>
          <p>
            Мы не смогли проверить сессию. Это может быть временный сбой соединения — текущая страница сохранена.
          </p>
          <button
            className="primary-btn"
            type="button"
            disabled={retrying}
            onClick={() => {
              setRetrying(true);
              void refresh().finally(() => setRetrying(false));
            }}
          >
            {retrying ? "Проверяем…" : "Повторить проверку"}
          </button>
        </section>
      </main>
    );
  }

  // Public auth pages must stay usable even while the API is waking up or
  // temporarily unreachable. Once the session check completes, authenticated
  // users are still redirected by the rules below.
  if (!ready && isPublic) {
    return <>{children}</>;
  }

  if (!ready) {
    return null;
  }

  if (!authenticated && !isPublic) {
    return null;
  }

  if (authenticated && isPublic) {
    return null;
  }

  if (authenticated && (!currentRole || isPublic || !roleAllowed)) {
    return null;
  }

  return <>{children}</>;
}
