"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useLocale } from "../../hooks/useLocale";
import { DEFAULT_API_BASE, normalizeApiBase, resolveApiBase } from "../../lib/apiBase";
import {
  type AppRole,
  destinationForRole,
  safeRelativePath,
} from "../../lib/authRedirect";
import { oauthErrorMessage } from "../../lib/oauthError";
import { clearSessionToken, setSessionToken } from "../../lib/sessionToken";
import styles from "../register/register.module.css";

const LS_API_BASE = "ops_api_base";
const SESSION_UPDATED_EVENT = "ops-session-updated";

function FeatureIcon({ name }: { name: "speed" | "shield" | "architecture" }) {
  if (name === "shield") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 5.5 5.7v5.1c0 4.4 2.6 7.8 6.5 9.7 3.9-1.9 6.5-5.3 6.5-9.7V5.7L12 3Z" /><path d="m9.2 11.8 1.8 1.8 3.8-4" /></svg>;
  }
  if (name === "architecture") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 18h14M7 15V9M12 15V5M17 15v-3" /><path d="m5 8 5-3 4 2 5-3" /></svg>;
  }
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4.5 15a7.5 7.5 0 1 1 15 0" /><path d="m12 15 4-5M8 18h8" /></svg>;
}

function ArrowIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M14 7l5 5-5 5" /></svg>;
}

function ChevronIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg>;
}

function EyeIcon({ hidden }: { hidden: boolean }) {
  return hidden
    ? <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M10.6 10.6A2 2 0 0 0 13.4 13.4M9.9 4.3A10.8 10.8 0 0 1 21 12a12.5 12.5 0 0 1-2.3 3.5M6.2 6.2A12.4 12.4 0 0 0 3 12c2.1 4 5.1 6 9 6 1.2 0 2.3-.2 3.3-.6" /></svg>
    : <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12c2.1-4 5.1-6 9-6s6.9 2 9 6c-2.1 4-5.1 6-9 6s-6.9-2-9-6Z" /><circle cx="12" cy="12" r="2.5" /></svg>;
}

function readRole(payload: unknown): AppRole | null {
  if (!payload || typeof payload !== "object") return null;
  const user = (payload as { user?: unknown }).user;
  if (!user || typeof user !== "object") return null;
  const role = (user as { role?: unknown }).role;
  return role === "admin" || role === "agency" || role === "client" || role === "solo_client" ? role : null;
}

function LoginPageContent() {
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
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const { locale } = useLocale();

  const inviteToken = useMemo(() => search.get("invite_token") || "", [search]);
  const requestedNext = useMemo(() => safeRelativePath(search.get("next"), "/"), [search]);
  const oauthError = useMemo(() => oauthErrorMessage(search.get("oauth_error")), [search]);

  function redirectAfterLogin(payload: unknown) {
    const role = readRole(payload);
    router.replace(role ? destinationForRole(role, requestedNext) : requestedNext);
  }

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
      const body = await res.json().catch(() => ({}));
      localStorage.setItem(LS_API_BASE, base);
      setSessionToken(t);
      window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
      redirectAfterLogin(body);
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
      clearSessionToken();
      window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
      redirectAfterLogin(body);
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
      redirectAfterLogin(body);
    } catch {
      setError("Не удалось войти по email и паролю");
    } finally {
      setLoading(false);
    }
  }

  function startOAuthLogin(provider: "facebook" | "google") {
    const base = normalizeApiBase(apiBase, defaultApiBase);
    localStorage.setItem(LS_API_BASE, base);
    clearSessionToken();
    window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
    const params = new URLSearchParams({ next: requestedNext, intent: "login" });
    window.location.href = `${base}/auth/${provider}/start?${params.toString()}`;
  }

  const tr = (en: string, ru: string) => locale === "en" ? en : ru;
  const features = [
    {
      icon: "speed" as const,
      title: tr("Financial speed in real time", "Финансовая скорость в реальном времени"),
      text: tr("Instant spend reconciliation for large advertising budgets.", "Мгновенная сверка расходов для больших рекламных бюджетов."),
    },
    {
      icon: "shield" as const,
      title: tr("Corporate control", "Корпоративный контроль"),
      text: tr("Deep compliance monitoring and immutable audit logs.", "Глубокий комплаенс-мониторинг и неизменяемые журналы аудита."),
    },
    {
      icon: "architecture" as const,
      title: tr("Architectural Ledger", "Архитектурный Ledger"),
      text: tr("Structured financial operations built to scale.", "Структурированные финансовые операции для масштабирования."),
    },
  ];

  return (
    <div className={styles.page} data-i18n-skip>
      <aside className={styles.hero}>
        <div className={styles.pattern} />
        <div className={styles.heroInner}>
          <Link className={styles.brand} href="/login">Envidicy</Link>
          <h1 className={styles.heroTitle}>
            {tr("Manage advertising operations", "Управляйте рекламными операциями")}
          </h1>
          <p className={styles.heroSubtitle}>
            {tr(
              "Accounts, top-ups, planning and reporting in one place",
              "Аккаунты, пополнения, планирование и отчетность в одном месте",
            )}
          </p>
          <div className={styles.featureList}>
            {features.map((item) => (
              <div className={styles.feature} key={item.title}>
                <span className={styles.featureIcon}><FeatureIcon name={item.icon} /></span>
                <div>
                  <p className={styles.featureTitle}>{item.title}</p>
                  <p className={styles.featureText}>{item.text}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className={styles.heroFooter}>
          <span>{tr("Trusted by leading platforms", "Нам доверяют ведущие платформы")}</span>
          <div className={styles.heroLine} />
        </div>
      </aside>

      <main className={styles.side}>
        <div className={styles.panel}>
          <nav className={styles.products} aria-label="Envidicy products">
            <a href="https://app.envidicy.kz">App Envidicy</a>
            <a className={styles.productActive} href="https://dash.envidicy.kz">Dash Envidicy</a>
            <a href="https://crm.envidicy.kz">CRM Envidicy</a>
          </nav>
          <span className={styles.mobileBrand}>Envidicy</span>
          <div className={styles.panelHead}>
            <h2 className={styles.panelTitle}>
              {inviteToken ? tr("Accept invitation", "Принять приглашение") : tr("Sign in", "Войти")}
            </h2>
            <p className={styles.panelText}>
              {inviteToken
                ? tr("Create a password to join the workspace.", "Создайте пароль, чтобы войти в рабочее пространство.")
                : tr("Enter your details to access your dashboard.", "Введите данные для входа в кабинет.")}
            </p>
          </div>

          <div className={styles.card}>
            {inviteToken ? (
              <form className={styles.form} onSubmit={(event) => { event.preventDefault(); void acceptInvite(); }}>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>{tr("Name", "Имя")}</span>
                  <input className={styles.fieldInput} value={inviteName} onChange={(event) => setInviteName(event.target.value)} placeholder={tr("Ivan Ivanov", "Иван Иванов")} />
                </label>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>{tr("Create password", "Придумайте пароль")}</span>
                  <input className={styles.fieldInput} type="password" value={invitePassword} onChange={(event) => setInvitePassword(event.target.value)} placeholder="********" />
                </label>
                <button className={styles.submit} type="submit" disabled={loading}>
                  <span>{loading ? tr("Accepting...", "Принимаем…") : tr("Accept invitation", "Принять приглашение")}</span>
                  <ArrowIcon />
                </button>
              </form>
            ) : (
              <form className={styles.form} onSubmit={(event) => { event.preventDefault(); void signInWithPassword(); }}>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>Email</span>
                  <input className={styles.fieldInput} type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@company.com" autoComplete="email" />
                </label>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>{tr("Password", "Пароль")}</span>
                  <span className={styles.fieldInputWrap}>
                    <input type={showPassword ? "text" : "password"} value={password} onChange={(event) => setPassword(event.target.value)} placeholder="********" autoComplete="current-password" />
                    <button className={styles.visibilityButton} type="button" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? tr("Hide password", "Скрыть пароль") : tr("Show password", "Показать пароль")}>
                      <EyeIcon hidden={showPassword} />
                    </button>
                  </span>
                </label>
                <button className={styles.submit} type="submit" disabled={loading}>
                  <span>{loading ? tr("Signing in...", "Входим…") : tr("Sign in", "Войти")}</span>
                  <ArrowIcon />
                </button>
                <div className={styles.divider}><span>OR</span></div>
                <button className={styles.metaButton} type="button" onClick={() => startOAuthLogin("facebook")} disabled={loading}>
                  <span className={styles.metaMark}>f</span>
                  <span>Continue with Meta</span>
                </button>
              </form>
            )}

            {tokenLoginEnabled ? (
              <div className={styles.tokenTools}>
                <div className={styles.divider}><span>{tr("internal access", "внутренний доступ")}</span></div>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>{tr("API address", "Адрес API")}</span>
                  <input className={styles.fieldInput} value={apiBase} onChange={(event) => setApiBase(event.target.value)} placeholder="http://127.0.0.1:8000" />
                </label>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>{tr("Session token", "Токен сессии")}</span>
                  <input className={styles.fieldInput} type="password" value={token} onChange={(event) => setToken(event.target.value)} />
                </label>
                <button className={styles.codeButton} type="button" onClick={() => void signInWithToken()} disabled={loading}>
                  {tr("Sign in with token", "Войти по токену")}
                </button>
              </div>
            ) : null}
          </div>

          <div className={styles.bottomLinks}>
            <button type="button" onClick={() => setError(tr("Open the password setup link from your invitation.", "Откройте ссылку установки пароля из приглашения."))}>
              <span>{tr("Set password", "Установить пароль")}</span>
              <ChevronIcon />
            </button>
            <span className={styles.dot} />
            <Link href="/register">
              <span>{tr("Need access?", "Нужен доступ?")}</span>
              <ChevronIcon />
            </Link>
          </div>
          <p className={styles.status}>{error || oauthError || ""}</p>
        </div>
      </main>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<main className="login-shell" />}>
      <LoginPageContent />
    </Suspense>
  );
}
