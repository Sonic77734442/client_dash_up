"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

const SESSION_UPDATED_EVENT = "ops-session-updated";

export default function LoginSuccessPage() {
  const router = useRouter();
  const search = useSearchParams();
  const [error, setError] = useState("");

  useEffect(() => {
    const next = search.get("next") || "/";
    window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
    router.replace(next.startsWith("/") ? next : "/");
  }, [router, search]);

  return (
    <main className="login-shell">
      <section className="login-card">
        <h1>Completing Sign In</h1>
        <p className="panel-subtitle">Finalizing OAuth callback and preparing your session.</p>
        <div className={`warning ${error ? "" : "hidden"}`}>{error}</div>
      </section>
    </main>
  );
}
