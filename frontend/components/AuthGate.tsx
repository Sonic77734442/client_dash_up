"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "../hooks/useAuth";
import { EnvidicyAccessGate } from "./EnvidicyAccessGate";
import { canManageEnvidicyBudgets, clearEnvidicyAutoLogin, envidicyAccessIssue, ENVIDICY_MY_URL, shouldRedirectToMy } from "../lib/envidicyAuth";
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
  const { ready, authenticated, role, me, error, refresh, logout } = useAuth(defaultApiBase);
  const [retrying, setRetrying] = useState(false);

  const currentPath = pathname || "";
  const isPublic = isPublicPath(currentPath);
  const currentRole: AppRole | null = isAppRole(role) ? role : null;
  const canManageLocalBudgets = canManageEnvidicyBudgets(me?.session);
  const roleAllowed = Boolean(currentRole && isPathAllowedForRole(currentRole, currentPath))
    || (canManageLocalBudgets && currentPath === "/budgets");
  const envidicyIssue = envidicyAccessIssue(me?.session);
  const redirectToMy = shouldRedirectToMy(me?.session);

  useEffect(() => {
    if (ready && authenticated && !error) clearEnvidicyAutoLogin();
  }, [ready, authenticated, error]);

  useEffect(() => {
    if (!ready) return;
    // This callback terminal force-checks the actual session before any return or My handoff.
    if (currentPath === "/login/success") return;
    if (!error && redirectToMy) {
      window.location.replace(ENVIDICY_MY_URL);
      return;
    }
    if (envidicyIssue && !isPublic) return;

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
      const safePath = safeRelativePath(requestedPath, "/portal");
      const budgetDestination = canManageLocalBudgets && new URL(safePath, window.location.origin).pathname === "/budgets";
      router.replace(budgetDestination ? safePath : destinationForRole(currentRole, requestedPath));
      return;
    }

    if (!roleAllowed) {
      router.replace(destinationForRole(currentRole));
    }
  }, [ready, authenticated, canManageLocalBudgets, currentPath, currentRole, envidicyIssue, error, isPublic, redirectToMy, roleAllowed, router]);

  if (currentPath === "/login/success") return <>{children}</>;

  if (ready && redirectToMy && !error) {
    return <main className="auth-outage-shell" role="status">Открываем My для настройки доступа…</main>;
  }

  if (ready && envidicyIssue && !isPublic) {
    return <EnvidicyAccessGate issue={envidicyIssue} refresh={refresh} logout={logout} />;
  }

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
