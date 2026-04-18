"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "../hooks/useAuth";

const PUBLIC_PATHS = new Set(["/login", "/login/success"]);

export function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const { ready, authenticated, role } = useAuth(defaultApiBase);

  const currentPath = pathname || "";
  const isPublic = PUBLIC_PATHS.has(currentPath);
  const isPlatformAdmin = currentPath.startsWith("/platform");

  useEffect(() => {
    if (!ready) return;

    if (!authenticated && !isPublic) {
      router.replace("/login");
      return;
    }

    if (authenticated && isPublic) {
      router.replace("/");
      return;
    }

    if (authenticated && isPlatformAdmin && role !== "admin") {
      router.replace("/");
    }
  }, [ready, authenticated, isPublic, isPlatformAdmin, role, router]);

  if (!ready) {
    return null;
  }

  if (!authenticated && !isPublic) {
    return null;
  }

  if (authenticated && isPublic) {
    return null;
  }

  if (authenticated && isPlatformAdmin && role !== "admin") {
    return null;
  }

  return <>{children}</>;
}
