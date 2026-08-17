"use client";

import { useEffect } from "react";
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
  const { ready, authenticated, role, error } = useAuth(defaultApiBase);

  const currentPath = pathname || "";
  const isPublic = isPublicPath(currentPath);
  const currentRole: AppRole | null = isAppRole(role) ? role : null;
  const roleAllowed = Boolean(currentRole && isPathAllowedForRole(currentRole, currentPath));

  useEffect(() => {
    if (!ready) return;

    if (error) {
      if (!authenticated && !isPublic) {
        const requestedPath = safeRelativePath(
          `${currentPath}${window.location.search}${window.location.hash}`,
          currentPath || "/",
        );
        router.replace(`/login?next=${encodeURIComponent(requestedPath)}`);
      }
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

  if (ready && error && !authenticated && !isPublic) {
    return null;
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
