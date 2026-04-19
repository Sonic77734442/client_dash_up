"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

const LS_SESSION_TOKEN = "ops_session_token";
const SESSION_UPDATED_EVENT = "ops-session-updated";

function parseHash() {
  const raw = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
  const p = new URLSearchParams(raw);
  return {
    token: p.get("token") || "",
    next: p.get("next") || "/",
  };
}

export default function LoginSuccessPage() {
  const router = useRouter();
  const search = useSearchParams();
  const [error, setError] = useState("");

  useEffect(() => {
    const { token: tokenFromHash, next: nextFromHash } = parseHash();
    const tokenFromQuery = search.get("token") || "";
    const token = tokenFromQuery || tokenFromHash;
    const next = search.get("next") || nextFromHash || "/";
    if (token) {
      localStorage.setItem(LS_SESSION_TOKEN, token);
    } else {
      localStorage.removeItem(LS_SESSION_TOKEN);
    }
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
