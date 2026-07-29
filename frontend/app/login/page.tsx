"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useLocale } from "../../hooks/useLocale";
import { Locale } from "../../lib/i18n";
import { DEFAULT_API_BASE, normalizeApiBase, resolveApiBase } from "../../lib/apiBase";
import { clearSessionToken, setSessionToken } from "../../lib/sessionToken";

const LS_API_BASE = "ops_api_base";
const SESSION_UPDATED_EVENT = "ops-session-updated";

export default function LoginPage() {
  const router = useRouter();
  const search = useSearchParams();
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";

  const [apiBase, setApiBase] = useState(normalizeApiBase(defaultApiBase, DEFAULT_API_BASE));
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
      setError("Укажите адрес API и токен сессии");
      return;
    }

    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${base}/auth/me`, {
        headers: { Authorization: `Bearer ${t}` },
      });
      if (!res.ok) {
        setError("Токен сессии недействителен");
        return;
      }
      localStorage.setItem(LS_API_BASE, base);
      setSessionToken(t);
      window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
      router.replace("/");
    } catch {
      setError("Не удалось войти");
    } finally {
      setLoading(false);
    }
  }

  async function acceptInvite() {
    const base = normalizeApiBase(apiBase, defaultApiBase);
    if (!base || !inviteToken) {
      setError("В приглашении отсутствует токен");
      return;
    }
    if (invitePassword.trim().length < 8) {
      setError("Пароль должен содержать не менее 8 символов");
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
        setError((body && body.error && body.error.message) || "Не удалось принять приглашение");
        return;
      }
      localStorage.setItem(LS_API_BASE, base);
      const issuedToken = String((body as { session?: { token?: string } })?.session?.token || "").trim();
      if (issuedToken) {
        setSessionToken(issuedToken);
      } else {
        clearSessionToken();
      }
      window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
      router.replace("/");
    } catch {
      setError("Не удалось принять приглашение");
    } finally {
      setLoading(false);
    }
  }

  async function signInWithPassword() {
    const base = normalizeApiBase(apiBase, defaultApiBase);
    const em = email.trim().toLowerCase();
    if (!base || !em || password.length < 8) {
      setError("Введите email и пароль не короче 8 символов");
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
        setError((body && body.error && body.error.message) || "Неверный email или пароль");
        return;
      }
      localStorage.setItem(LS_API_BASE, base);
      clearSessionToken();
      window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
      router.replace("/");
    } catch {
      setError("Не удалось войти по email и паролю");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-card">
        <h1>Вход</h1>
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 6 }}>
          <select
            className="locale-switch"
            value={locale}
            onChange={(e) => setLocale(e.target.value as Locale)}
            aria-label="Язык"
            title="Язык"
          >
            <option value="en">EN</option>
            <option value="ru">RU</option>
          </select>
        </div>
        <p className="panel-subtitle">Войдите удобным для вас способом.</p>

        {tokenLoginEnabled ? (
          <>
            <div className="login-divider">Вход по внутреннему токену</div>
            <label>
              Адрес API
              <input value={apiBase} onChange={(e) => setApiBase(e.target.value)} placeholder="http://127.0.0.1:8000" />
            </label>

            <label>
              Токен сессии
              <input
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="Вставьте токен сессии"
              />
            </label>

            <button className="primary-btn" onClick={() => void signInWithToken()} disabled={loading}>
              {loading ? "Входим…" : "Войти"}
            </button>
          </>
        ) : null}

        <div className="login-divider">Email и пароль</div>
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" />
        </label>
        <label>
          Пароль
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Минимум 8 символов" />
        </label>
        <button className="primary-btn" onClick={() => void signInWithPassword()} disabled={loading}>
          {loading ? "Входим…" : "Войти по паролю"}
        </button>

        <div className={`warning ${error ? "" : "hidden"}`}>{error}</div>

        {inviteToken ? (
          <>
            <div className="login-divider">Приглашение в агентство</div>
            <label>
              Ваше имя (необязательно)
              <input value={inviteName} onChange={(e) => setInviteName(e.target.value)} placeholder="Иван Иванов" />
            </label>
            <label>
              Придумайте пароль
              <input
                type="password"
                value={invitePassword}
                onChange={(e) => setInvitePassword(e.target.value)}
                placeholder="Минимум 8 символов"
              />
            </label>
            <button className="primary-btn" onClick={() => void acceptInvite()} disabled={loading}>
              {loading ? "Принимаем…" : "Принять приглашение"}
            </button>
          </>
        ) : null}

        <div className="login-divider">Вход через сервис</div>
        <div className="login-oauth-row">
          <button
            className="ghost-btn"
            onClick={() => {
              const base = normalizeApiBase(apiBase, defaultApiBase);
              localStorage.setItem(LS_API_BASE, base);
              clearSessionToken();
              window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
              window.location.href = `${base}/auth/facebook/start?next=/`;
            }}
          >
            Продолжить через Facebook
          </button>
          <button
            className="ghost-btn"
            onClick={() => {
              const base = normalizeApiBase(apiBase, defaultApiBase);
              localStorage.setItem(LS_API_BASE, base);
              clearSessionToken();
              window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
              window.location.href = `${base}/auth/google/start?next=/`;
            }}
          >
            Продолжить через Google
          </button>
        </div>
      </section>
    </main>
  );
}
