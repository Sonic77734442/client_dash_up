"use client";

import Link from "next/link";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useLocale } from "../../hooks/useLocale";
import { type Locale } from "../../lib/i18n";
import { DEFAULT_API_BASE, normalizeApiBase, resolveApiBase } from "../../lib/apiBase";
import {
  type AppRole,
  destinationForRole,
  safeRelativePath,
} from "../../lib/authRedirect";
import { oauthErrorMessage } from "../../lib/oauthError";
import { oauthRelayLaunchPath } from "../../lib/oauthLaunchRelay";
import { clearSessionToken, setSessionToken } from "../../lib/sessionToken";
import styles from "./login.module.css";

const LS_API_BASE = "ops_api_base";
const SESSION_UPDATED_EVENT = "ops-session-updated";

function FeatureIcon({ name }: { name: "overview" | "signal" | "action" }) {
  if (name === "signal") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M4.5 15a7.5 7.5 0 1 1 15 0" />
        <path d="m12 15 4-5M8 18h8" />
      </svg>
    );
  }
  if (name === "action") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M5 18h14M7 15V9M12 15V5M17 15v-3" />
        <path d="m5 8 5-3 4 2 5-3" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="4" y="5" width="16" height="14" rx="3" />
      <path d="M8 14v2M12 10v6M16 12v4" />
    </svg>
  );
}

function ArrowIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 12h14M14 7l5 5-5 5" />
    </svg>
  );
}

function ChevronIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="m9 6 6 6-6 6" />
    </svg>
  );
}

function EyeIcon({ hidden }: { hidden: boolean }) {
  return hidden ? (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3 3l18 18M10.6 10.6A2 2 0 0 0 13.4 13.4M9.9 4.3A10.8 10.8 0 0 1 21 12a12.5 12.5 0 0 1-2.3 3.5M6.2 6.2A12.4 12.4 0 0 0 3 12c2.1 4 5.1 6 9 6 1.2 0 2.3-.2 3.3-.6" />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3 12c2.1-4 5.1-6 9-6s6.9 2 9 6c-2.1 4-5.1 6-9 6s-6.9-2-9-6Z" />
      <circle cx="12" cy="12" r="2.5" />
    </svg>
  );
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
  const { locale, setLocale } = useLocale();

  const inviteToken = useMemo(() => search.get("invite_token") || "", [search]);
  const requestedNext = useMemo(() => safeRelativePath(search.get("next"), "/"), [search]);
  const oauthErrorCode = useMemo(
    () => String(search.get("oauth_error") || "").trim().toLowerCase(),
    [search],
  );
  const oauthError = useMemo(() => oauthErrorMessage(oauthErrorCode), [oauthErrorCode]);
  const needsFacebookMigration = oauthErrorCode === "facebook_migration_required";
  const tr = (en: string, ru: string) => locale === "en" ? en : ru;

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

  function startOAuthLogin(
    provider: "facebook" | "google",
    intent: "login" | "migrate" = "login",
  ) {
    const base = normalizeApiBase(apiBase, defaultApiBase);
    localStorage.setItem(LS_API_BASE, base);
    clearSessionToken();
    window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
    const params = new URLSearchParams({ next: requestedNext, intent });
    window.location.href = base.startsWith("/")
      ? oauthRelayLaunchPath(provider, params)
      : `${base}/auth/${provider}/start?${params.toString()}`;
  }

  const features = [
    {
      icon: "overview" as const,
      title: tr("Every client and channel in one view", "Все клиенты и площадки в одном окне"),
      text: tr(
        "Google Ads, Meta and campaign performance without switching between cabinets.",
        "Google Ads, Meta и показатели кампаний — без переключения между кабинетами.",
      ),
    },
    {
      icon: "signal" as const,
      title: tr("Deviations are visible immediately", "Отклонения видны сразу"),
      text: tr(
        "Plan, actuals and anomalies are collected into a single operational picture.",
        "План, факт и аномалии собраны в единую операционную картину.",
      ),
    },
    {
      icon: "action" as const,
      title: tr("From data to the next action", "От данных — к следующему действию"),
      text: tr(
        "Clear reasons, priorities and reports for agency and client teams.",
        "Понятные причины, приоритеты и отчёты для агентства и клиента.",
      ),
    },
  ];

  const statusMessage = error || oauthError;

  return (
    <div className={styles.page} data-i18n-skip>
      <aside className={styles.hero} aria-label={tr("About Dash Envidicy", "О Dash Envidicy")}>
        <div className={styles.pattern} />
        <div className={styles.heroInner}>
          <Link className={styles.brand} href="/login" aria-label="Envidicy">Envidicy</Link>
          <span className={styles.productLabel}>Dash</span>
          <h1 className={styles.heroTitle}>
            {tr("Run advertising operations with clarity", "Управляйте рекламными операциями")}
          </h1>
          <p className={styles.heroSubtitle}>
            {tr(
              "Clients, advertising accounts, campaigns, deviations and reports in one place.",
              "Клиенты, рекламные аккаунты, кампании, отклонения и отчёты — в одном месте.",
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
          <span>{tr("One Envidicy ecosystem", "Единая экосистема Envidicy")}</span>
          <div className={styles.heroLine} />
        </div>
      </aside>

      <main className={styles.side}>
        <div className={styles.panel}>
          <nav className={styles.products} aria-label={tr("Envidicy products", "Продукты Envidicy")}>
            <a href="https://app.envidicy.kz">App Envidicy</a>
            <span className={styles.productActive} aria-current="page">Dash Envidicy</span>
            <a href="https://crm.envidicy.kz">CRM Envidicy</a>
          </nav>

          <div className={styles.mobileTopline}>
            <span className={styles.mobileBrand}>Envidicy</span>
            <label className={styles.localeControl}>
              <span className={styles.srOnly}>{tr("Language", "Язык")}</span>
              <select
                value={locale}
                onChange={(event) => setLocale(event.target.value as Locale)}
                aria-label={tr("Language", "Язык")}
              >
                <option value="ru">RU</option>
                <option value="en">EN</option>
              </select>
            </label>
          </div>

          <div className={styles.panelHead}>
            <div>
              <p className={styles.eyebrow}>Dash Envidicy</p>
              <h2 className={styles.panelTitle}>
                {inviteToken ? tr("Accept invitation", "Принять приглашение") : tr("Sign in", "Войти")}
              </h2>
              <p className={styles.panelText}>
                {inviteToken
                  ? tr("Create a password to join the workspace.", "Создайте пароль, чтобы войти в рабочее пространство.")
                  : tr("Enter your details to access the advertising operations center.", "Войдите в операционный центр управления рекламой.")}
              </p>
            </div>
            <label className={`${styles.localeControl} ${styles.desktopLocale}`}>
              <span className={styles.srOnly}>{tr("Language", "Язык")}</span>
              <select
                value={locale}
                onChange={(event) => setLocale(event.target.value as Locale)}
                aria-label={tr("Language", "Язык")}
              >
                <option value="ru">RU</option>
                <option value="en">EN</option>
              </select>
            </label>
          </div>

          <section className={styles.card} aria-label={inviteToken ? tr("Invitation", "Приглашение") : tr("Sign in form", "Форма входа")}>
            {inviteToken ? (
              <form className={styles.form} onSubmit={(event) => { event.preventDefault(); void acceptInvite(); }}>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>{tr("Name", "Имя")}</span>
                  <input
                    className={styles.fieldInput}
                    value={inviteName}
                    onChange={(event) => setInviteName(event.target.value)}
                    placeholder={tr("Ivan Ivanov", "Иван Иванов")}
                    autoComplete="name"
                  />
                </label>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>{tr("Create password", "Придумайте пароль")}</span>
                  <input
                    className={styles.fieldInput}
                    type="password"
                    value={invitePassword}
                    onChange={(event) => setInvitePassword(event.target.value)}
                    placeholder={tr("At least 8 characters", "Минимум 8 символов")}
                    autoComplete="new-password"
                  />
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
                  <input
                    className={styles.fieldInput}
                    type="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    placeholder="name@company.com"
                    autoComplete="email"
                  />
                </label>
                <div className={styles.field}>
                  <label className={styles.fieldLabel} htmlFor="login-password">
                    {tr("Password", "Пароль")}
                  </label>
                  <span className={styles.fieldInputWrap}>
                    <input
                      id="login-password"
                      type={showPassword ? "text" : "password"}
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      placeholder={tr("At least 8 characters", "Минимум 8 символов")}
                      autoComplete="current-password"
                    />
                    <button
                      className={styles.visibilityButton}
                      type="button"
                      onClick={() => setShowPassword((value) => !value)}
                      aria-label={showPassword ? tr("Hide password", "Скрыть пароль") : tr("Show password", "Показать пароль")}
                    >
                      <EyeIcon hidden={showPassword} />
                    </button>
                  </span>
                </div>
                <button className={styles.submit} type="submit" disabled={loading}>
                  <span>{loading ? tr("Signing in...", "Входим…") : tr("Sign in", "Войти")}</span>
                  <ArrowIcon />
                </button>

                <div className={styles.divider}><span>{tr("or", "или")}</span></div>

                <p className={styles.oauthHint}>
                  {tr(
                    "Your first Facebook sign-in creates a client workspace immediately, without approval. Facebook and Google are used only to sign in; advertising accounts are connected later in Advertising sources.",
                    "Первый вход через Facebook сразу создаст ваш клиентский кабинет — без ожидания подтверждения. Facebook и Google здесь используются только для входа; рекламные кабинеты подключаются позже в разделе «Источники рекламы».",
                  )}
                </p>

                <div className={styles.providerGrid}>
                  <button
                    className={styles.providerButton}
                    type="button"
                    onClick={() => startOAuthLogin("facebook")}
                    disabled={loading}
                    aria-label={tr("Sign in with Facebook", "Войти через Facebook")}
                  >
                    <span className={styles.metaMark}>f</span>
                    <span>{tr("Continue with Meta", "Продолжить с Meta")}</span>
                  </button>
                  <button
                    className={styles.providerButton}
                    type="button"
                    onClick={() => startOAuthLogin("google")}
                    disabled={loading}
                    aria-label={tr("Sign in with Google", "Войти через Google")}
                  >
                    <span className={styles.googleMark}>G</span>
                    <span>{tr("Continue with Google", "Продолжить с Google")}</span>
                  </button>
                </div>

                {needsFacebookMigration ? (
                  <div className={styles.migrationNotice} role="status">
                    <p>
                      {tr(
                        "Confirm the old and new Facebook sign-ins once. We will keep your current role, clients and history.",
                        "Один раз подтвердите старый и новый вход Facebook. Текущая роль, клиенты и история сохранятся.",
                      )}
                    </p>
                    <button
                      className={styles.migrationButton}
                      type="button"
                      onClick={() => startOAuthLogin("facebook", "migrate")}
                      disabled={loading}
                    >
                      {tr("Transfer Facebook sign-in", "Перенести Facebook-вход")}
                    </button>
                  </div>
                ) : null}
              </form>
            )}

            {tokenLoginEnabled ? (
              <div className={styles.tokenTools}>
                <div className={styles.divider}><span>{tr("internal access", "внутренний доступ")}</span></div>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>{tr("API address", "Адрес API")}</span>
                  <input
                    className={styles.fieldInput}
                    value={apiBase}
                    onChange={(event) => setApiBase(event.target.value)}
                    placeholder="http://127.0.0.1:8000"
                  />
                </label>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>{tr("Session token", "Токен сессии")}</span>
                  <input
                    className={styles.fieldInput}
                    type="password"
                    value={token}
                    onChange={(event) => setToken(event.target.value)}
                  />
                </label>
                <button className={styles.codeButton} type="button" onClick={() => void signInWithToken()} disabled={loading}>
                  {tr("Sign in with token", "Войти по токену")}
                </button>
              </div>
            ) : null}
          </section>

          <div className={styles.bottomLinks}>
            <button
              type="button"
              onClick={() => setError(tr("Open the password setup link from your invitation.", "Откройте ссылку установки пароля из приглашения."))}
            >
              <span>{tr("Set password", "Установить пароль")}</span>
              <ChevronIcon />
            </button>
            <span className={styles.dot} />
            <button
              type="button"
              onClick={() => setError(tr("Use Meta or Google for instant access, or ask your administrator for an invitation.", "Войдите через Meta или Google без ожидания либо запросите приглашение у администратора."))}
            >
              <span>{tr("Need access?", "Нужен доступ?")}</span>
              <ChevronIcon />
            </button>
          </div>

          <p className={`${styles.status} ${statusMessage ? styles.statusVisible : ""}`} role={statusMessage ? "alert" : undefined}>
            {statusMessage || ""}
          </p>
        </div>
      </main>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<main className={styles.loadingShell} aria-label="Loading" />}>
      <LoginPageContent />
    </Suspense>
  );
}
