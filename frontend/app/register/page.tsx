"use client";

import Link from "next/link";
import { FormEvent, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useLocale } from "../../hooks/useLocale";
import { normalizeApiBase, resolveApiBase } from "../../lib/apiBase";
import { clearSessionToken } from "../../lib/sessionToken";
import styles from "./register.module.css";

type FeatureIconName = "speed" | "shield" | "scale";

function FeatureIcon({ name }: { name: FeatureIconName }) {
  if (name === "shield") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 5 6v5c0 4.6 2.8 8.1 7 10 4.2-1.9 7-5.4 7-10V6l-7-3Z" /><path d="m9 12 2 2 4-4" /></svg>;
  }
  if (name === "scale") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 18h16M6 15V9M12 15V5M18 15v-3" /><path d="m4 8 5-3 5 2 6-4" /></svg>;
  }
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 15a8 8 0 1 1 16 0" /><path d="m12 15 4-5M7 18h10" /></svg>;
}

function ArrowIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M14 7l5 5-5 5" /></svg>;
}

function ChevronIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg>;
}

function errorMessage(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object") return fallback;
  const value = body as { error?: { message?: unknown }; detail?: unknown };
  if (typeof value.error?.message === "string") return value.error.message;
  if (typeof value.detail === "string") return value.detail;
  if (value.detail && typeof value.detail === "object") {
    const message = (value.detail as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  return fallback;
}

export default function RegisterPage() {
  const router = useRouter();
  const { locale } = useLocale();
  const isRu = locale === "ru";
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const apiBase = useMemo(() => resolveApiBase(defaultApiBase), [defaultApiBase]);
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [phoneVerified, setPhoneVerified] = useState(false);
  const [verificationToken, setVerificationToken] = useState("");
  const [pending, setPending] = useState<"send" | "confirm" | "register" | "">("");
  const [status, setStatus] = useState("");
  const [statusKind, setStatusKind] = useState<"error" | "success" | "info">("info");

  const copy = isRu ? {
    heroTitle: "Один кабинет. Все результаты.",
    heroText: "Создайте рабочее пространство и держите аналитику, финансы и доступы под контролем.",
    features: [
      ["speed", "Всё в одном месте", "Аналитика по всем подключённым рекламным площадкам."],
      ["shield", "Безопасный доступ", "Подтверждение телефона защищает аккаунт с первого входа."],
      ["scale", "Готово к росту", "Добавляйте кабинеты, команды и новые источники данных."],
    ],
    secure: "Безопасная регистрация",
    title: "Создать аккаунт",
    subtitle: "Заполните данные и подтвердите номер через WhatsApp.",
    name: "Имя",
    namePlaceholder: "Анна Маркетолог",
    company: "Компания",
    phone: "Номер телефона",
    sendCode: "Отправить код",
    resend: "Отправить ещё",
    sending: "Отправляем…",
    code: "Код подтверждения из WhatsApp",
    confirmCode: "Подтвердить",
    checking: "Проверяем…",
    verified: "Подтверждено",
    password: "Пароль",
    confirmPassword: "Повторите пароль",
    create: "Создать аккаунт",
    creating: "Создаём…",
    or: "или",
    meta: "Продолжить через Meta",
    existing: "Уже есть аккаунт",
    footer: "Мы проверим данные и создадим ваше рабочее пространство.",
  } : {
    heroTitle: "One dashboard. Every result.",
    heroText: "Create your workspace and keep analytics, finances and access under control.",
    features: [
      ["speed", "Everything in one place", "Analytics across every connected advertising platform."],
      ["shield", "Secure access", "Phone verification protects your account from the first sign-in."],
      ["scale", "Ready to scale", "Add accounts, teams and new data sources as you grow."],
    ],
    secure: "Secure registration",
    title: "Create account",
    subtitle: "Enter your details and verify your WhatsApp number.",
    name: "Name",
    namePlaceholder: "Anna Marketer",
    company: "Company",
    phone: "Phone number",
    sendCode: "Send code",
    resend: "Send again",
    sending: "Sending…",
    code: "WhatsApp verification code",
    confirmCode: "Confirm",
    checking: "Checking…",
    verified: "Verified",
    password: "Password",
    confirmPassword: "Confirm password",
    create: "Create account",
    creating: "Creating…",
    or: "or",
    meta: "Continue with Meta",
    existing: "Already have an account",
    footer: "We will verify your details and create your workspace.",
  };

  function showStatus(message: string, kind: "error" | "success" | "info" = "info") {
    setStatus(message);
    setStatusKind(kind);
  }

  function onPhoneChange(value: string) {
    setPhone(value);
    setCodeSent(false);
    setCode("");
    setPhoneVerified(false);
    setVerificationToken("");
  }

  async function sendCode() {
    if (!phone.trim()) {
      showStatus(isRu ? "Введите номер телефона." : "Enter your phone number.", "error");
      return;
    }
    setPending("send");
    showStatus(isRu ? "Отправляем код в WhatsApp…" : "Sending a code to WhatsApp…");
    try {
      const response = await fetch(`${normalizeApiBase(apiBase, defaultApiBase)}/auth/phone-verification/send`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorMessage(body, isRu ? "Не удалось отправить код." : "Could not send the code."));
      setCodeSent(true);
      setCode("");
      showStatus(isRu ? "Код отправлен в WhatsApp и действует 10 минут." : "The code was sent to WhatsApp and is valid for 10 minutes.", "success");
    } catch (error) {
      showStatus(error instanceof Error ? error.message : (isRu ? "Не удалось отправить код." : "Could not send the code."), "error");
    } finally {
      setPending("");
    }
  }

  async function confirmCode() {
    if (!/^\d{6}$/.test(code)) {
      showStatus(isRu ? "Введите шестизначный код." : "Enter the 6-digit code.", "error");
      return;
    }
    setPending("confirm");
    try {
      const response = await fetch(`${normalizeApiBase(apiBase, defaultApiBase)}/auth/phone-verification/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone, code }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorMessage(body, isRu ? "Неверный код." : "The code is incorrect."));
      setVerificationToken(String(body.verification_token || ""));
      setPhoneVerified(true);
      showStatus(isRu ? "Номер телефона подтверждён." : "Phone number verified.", "success");
    } catch (error) {
      showStatus(error instanceof Error ? error.message : (isRu ? "Не удалось подтвердить код." : "Could not verify the code."), "error");
    } finally {
      setPending("");
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (password !== confirmPassword) {
      showStatus(isRu ? "Пароли не совпадают." : "Passwords do not match.", "error");
      return;
    }
    if (!phoneVerified || !verificationToken) {
      showStatus(isRu ? "Сначала подтвердите номер телефона." : "Verify your phone number first.", "error");
      return;
    }
    setPending("register");
    try {
      const response = await fetch(`${normalizeApiBase(apiBase, defaultApiBase)}/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          name: name.trim(),
          company: company.trim() || null,
          phone,
          email: email.trim().toLowerCase(),
          password,
          phone_verification_token: verificationToken,
        }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorMessage(body, isRu ? "Не удалось создать аккаунт." : "Could not create the account."));
      clearSessionToken();
      localStorage.setItem("ops_api_base", normalizeApiBase(apiBase, defaultApiBase));
      window.dispatchEvent(new Event("ops-session-updated"));
      router.replace("/portal");
    } catch (error) {
      showStatus(error instanceof Error ? error.message : (isRu ? "Не удалось создать аккаунт." : "Could not create the account."), "error");
      setPending("");
    }
  }

  function startMeta() {
    localStorage.setItem("ops_api_base", normalizeApiBase(apiBase, defaultApiBase));
    window.location.href = `${normalizeApiBase(apiBase, defaultApiBase)}/auth/facebook/start?next=/portal&intent=login`;
  }

  return (
    <div className={styles.page}>
      <aside className={styles.hero}>
        <div className={styles.pattern} />
        <div className={styles.heroInner}>
          <Link className={styles.brand} href="/login">Envidicy</Link>
          <h1>{copy.heroTitle}</h1>
          <p className={styles.heroSubtitle}>{copy.heroText}</p>
          <div className={styles.featureList}>
            {copy.features.map(([icon, title, text]) => (
              <div className={styles.feature} key={title}>
                <span className={styles.featureIcon}><FeatureIcon name={icon as FeatureIconName} /></span>
                <div><strong>{title}</strong><p>{text}</p></div>
              </div>
            ))}
          </div>
        </div>
        <div className={styles.heroFooter}><span>{copy.secure}</span><i /></div>
      </aside>

      <main className={styles.side}>
        <div className={styles.panel}>
          <nav className={styles.products} aria-label="Envidicy products">
            <a href="https://app.envidicy.kz">App Envidicy</a>
            <a className={styles.productActive} href="https://dash.envidicy.kz">Dash Envidicy</a>
            <a href="https://crm.envidicy.kz">CRM Envidicy</a>
          </nav>
          <span className={styles.mobileBrand}>Envidicy</span>
          <header className={styles.panelHead}>
            <h2>{copy.title}</h2>
            <p>{copy.subtitle}</p>
          </header>

          <section className={styles.card}>
            <form className={styles.form} onSubmit={submit}>
              <div className={styles.fieldGrid}>
                <label><span>{copy.name}</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder={copy.namePlaceholder} required /></label>
                <label><span>{copy.company}</span><input value={company} onChange={(event) => setCompany(event.target.value)} placeholder="ACME Corp" /></label>
              </div>
              <label>
                <span>{copy.phone}</span>
                <div className={styles.codeRow}>
                  <input value={phone} onChange={(event) => onPhoneChange(event.target.value)} type="tel" inputMode="tel" autoComplete="tel" placeholder="+7 700 000 00 00" required />
                  <button type="button" className={styles.codeButton} onClick={() => void sendCode()} disabled={pending !== "" || phoneVerified}>
                    {pending === "send" ? copy.sending : phoneVerified ? copy.verified : codeSent ? copy.resend : copy.sendCode}
                  </button>
                </div>
              </label>
              {codeSent && !phoneVerified ? (
                <label>
                  <span>{copy.code}</span>
                  <div className={styles.codeRow}>
                    <input value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))} inputMode="numeric" autoComplete="one-time-code" placeholder="000000" />
                    <button type="button" className={styles.codeButton} onClick={() => void confirmCode()} disabled={pending !== "" || code.length !== 6}>
                      {pending === "confirm" ? copy.checking : copy.confirmCode}
                    </button>
                  </div>
                </label>
              ) : null}
              <label><span>Email</span><input value={email} onChange={(event) => setEmail(event.target.value)} type="email" autoComplete="email" placeholder="name@company.com" required /></label>
              <div className={styles.fieldGrid}>
                <label><span>{copy.password}</span><input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="new-password" placeholder="********" minLength={8} required /></label>
                <label><span>{copy.confirmPassword}</span><input value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} type="password" autoComplete="new-password" placeholder="********" minLength={8} required /></label>
              </div>
              <button className={styles.submit} type="submit" disabled={pending !== "" || !phoneVerified}>
                <span>{pending === "register" ? copy.creating : copy.create}</span><ArrowIcon />
              </button>
              <div className={styles.divider}><span>{copy.or}</span></div>
              <button className={styles.metaButton} type="button" onClick={startMeta} disabled={pending !== ""}>
                <b>f</b><span>{copy.meta}</span>
              </button>
            </form>
          </section>

          <div className={styles.bottomLinks}><Link href="/login"><span>{copy.existing}</span><ChevronIcon /></Link></div>
          <p className={`${styles.status} ${styles[statusKind]}`}>{status || copy.footer}</p>
        </div>
      </main>
    </div>
  );
}

