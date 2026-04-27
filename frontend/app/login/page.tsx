"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useLocale } from "../../hooks/useLocale";
import { Locale } from "../../lib/i18n";
import { normalizeApiBase, resolveApiBase } from "../../lib/apiBase";

const LS_API_BASE = "ops_api_base";
const LS_SESSION_TOKEN = "ops_session_token";
const SESSION_UPDATED_EVENT = "ops-session-updated";

export default function LoginPage() {
  const router = useRouter();
  const search = useSearchParams();
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";

  const [apiBase, setApiBase] = useState(normalizeApiBase(defaultApiBase, "http://localhost:8000"));
  const [token, setToken] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [inviteName, setInviteName] = useState("");
  const [invitePassword, setInvitePassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const { locale, setLocale } = useLocale();

  const inviteToken = useMemo(() => search.get("invite_token") || "", [search]);

  useEffect(() => {
    const base = resolveApiBase(defaultApiBase);
    setApiBase(base);
  }, [defaultApiBase]);

  async function signInWithToken() {
    const base = normalizeApiBase(apiBase, defaultApiBase);
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
    const base = normalizeApiBase(apiBase, defaultApiBase);
    if (!base || !inviteToken) {
      setError("Invite token is missing");
      return;
    }
    if (invitePassword.trim().length < 8) {
      setError("Password must be at least 8 characters");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${base}/auth/invites/accept`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ token: inviteToken, name: inviteName.trim() || undefined, password: invitePassword }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError((body && body.error && body.error.message) || "Invite accept failed");
        return;
      }
      localStorage.setItem(LS_API_BASE, base);
      const issuedToken = String((body as { session?: { token?: string } })?.session?.token || "").trim();
      if (issuedToken) {
        localStorage.setItem(LS_SESSION_TOKEN, issuedToken);
      } else {
        localStorage.removeItem(LS_SESSION_TOKEN);
      }
      window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
      router.replace("/");
    } catch {
      setError("Invite accept failed");
    } finally {
      setLoading(false);
    }
  }

  async function signInWithPassword() {
    const base = normalizeApiBase(apiBase, defaultApiBase);
    const em = email.trim().toLowerCase();
    if (!base || !em || password.length < 8) {
      setError("Email and password (min 8 chars) are required");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${base}/auth/password/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email: em, password }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError((body && body.error && body.error.message) || "Email/password login failed");
        return;
      }
      localStorage.setItem(LS_API_BASE, base);
      localStorage.removeItem(LS_SESSION_TOKEN);
      window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
      router.replace("/");
    } catch {
      setError("Email/password login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-card">
        <h1>Sign In</h1>
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 6 }}>
          <select
            className="locale-switch"
            value={locale}
            onChange={(e) => setLocale(e.target.value as Locale)}
            aria-label="Language"
            title="Language"
          >
            <option value="en">EN</option>
            <option value="ru">RU</option>
          </select>
        </div>
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

        <div className="login-divider">Email and password</div>
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters" />
        </label>
        <button className="primary-btn" onClick={() => void signInWithPassword()} disabled={loading}>
          {loading ? "Signing in..." : "Sign In with Password"}
        </button>

        <div className={`warning ${error ? "" : "hidden"}`}>{error}</div>

        {inviteToken ? (
          <>
            <div className="login-divider">Agency invite</div>
            <label>
              Your name (optional)
              <input value={inviteName} onChange={(e) => setInviteName(e.target.value)} placeholder="John Doe" />
            </label>
            <label>
              Set password
              <input
                type="password"
                value={invitePassword}
                onChange={(e) => setInvitePassword(e.target.value)}
                placeholder="At least 8 characters"
              />
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
              const base = normalizeApiBase(apiBase, defaultApiBase);
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
              const base = normalizeApiBase(apiBase, defaultApiBase);
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
