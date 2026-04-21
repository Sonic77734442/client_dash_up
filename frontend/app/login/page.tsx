"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

const LS_API_BASE = "ops_api_base";
const LS_SESSION_TOKEN = "ops_session_token";
const SESSION_UPDATED_EVENT = "ops-session-updated";

export default function LoginPage() {
  const router = useRouter();
  const search = useSearchParams();
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";

  const [apiBase, setApiBase] = useState(defaultApiBase);
  const [token, setToken] = useState("");
  const [inviteName, setInviteName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const inviteToken = useMemo(() => search.get("invite_token") || "", [search]);

  useEffect(() => {
    const base = localStorage.getItem(LS_API_BASE) || defaultApiBase;
    setApiBase(base);
  }, [defaultApiBase]);

  async function signInWithToken() {
    const base = apiBase.trim().replace(/\/$/, "");
    const t = token.trim();
    if (!base || !t) {
      setError("API base and session token are required");
      return;
    }

    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${base}/auth/me`, {
        headers: { Authorization: `Bearer ${t}` },
      });
      if (!res.ok) {
        setError("Invalid session token");
        return;
      }
      localStorage.setItem(LS_API_BASE, base);
      localStorage.setItem(LS_SESSION_TOKEN, t);
      window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
      router.replace("/");
    } catch {
      setError("Login failed");
    } finally {
      setLoading(false);
    }
  }

  async function acceptInvite() {
    const base = apiBase.trim().replace(/\/$/, "");
    if (!base || !inviteToken) {
      setError("Invite token is missing");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${base}/auth/invites/accept`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ token: inviteToken, name: inviteName.trim() || undefined }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError((body && body.error && body.error.message) || "Invite accept failed");
        return;
      }
      localStorage.setItem(LS_API_BASE, base);
      localStorage.removeItem(LS_SESSION_TOKEN);
      window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
      router.replace("/");
    } catch {
      setError("Invite accept failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-card">
        <h1>Sign In</h1>
        <p className="panel-subtitle">Sign in using your connected provider.</p>

        {tokenLoginEnabled ? (
          <>
            <div className="login-divider">Internal token login</div>
            <label>
              API base
              <input value={apiBase} onChange={(e) => setApiBase(e.target.value)} placeholder="http://127.0.0.1:8000" />
            </label>

            <label>
              Session token
              <input
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="Paste session token"
              />
            </label>

            <button className="primary-btn" onClick={() => void signInWithToken()} disabled={loading}>
              {loading ? "Signing in..." : "Sign In"}
            </button>
          </>
        ) : null}

        <div className={`warning ${error ? "" : "hidden"}`}>{error}</div>

        {inviteToken ? (
          <>
            <div className="login-divider">Agency invite</div>
            <label>
              Your name (optional)
              <input value={inviteName} onChange={(e) => setInviteName(e.target.value)} placeholder="John Doe" />
            </label>
            <button className="primary-btn" onClick={() => void acceptInvite()} disabled={loading}>
              {loading ? "Accepting..." : "Accept Invite"}
            </button>
          </>
        ) : null}

        <div className="login-divider">OAuth providers</div>
        <div className="login-oauth-row">
          <button
            className="ghost-btn"
            onClick={() => {
              const base = apiBase.trim().replace(/\/$/, "");
              localStorage.setItem(LS_API_BASE, base);
              localStorage.removeItem(LS_SESSION_TOKEN);
              window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
              window.location.href = `${base}/auth/facebook/start?next=/`;
            }}
          >
            Continue with Facebook
          </button>
          <button
            className="ghost-btn"
            onClick={() => {
              const base = apiBase.trim().replace(/\/$/, "");
              localStorage.setItem(LS_API_BASE, base);
              localStorage.removeItem(LS_SESSION_TOKEN);
              window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
              window.location.href = `${base}/auth/google/start?next=/`;
            }}
          >
            Continue with Google
          </button>
        </div>
      </section>
    </main>
  );
}
