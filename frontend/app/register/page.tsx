"use client";

import Link from "next/link";
import { FormEvent, useMemo, useState } from "react";
import { useLocale } from "../../hooks/useLocale";
import styles from "./register.module.css";

function FeatureIcon({ name }: { name: string }) {
  if (name === "verified_user") {
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

export default function RegisterPage() {
  const { locale } = useLocale();
  const tr = (en: string, ru: string) => locale === "en" ? en : ru;
  const [status] = useState(() => tr(
    "We will verify your details and send an invitation.",
    "Мы проверим данные и отправим приглашение.",
  ));
  const features = useMemo(() => [
    {
      icon: "speed",
      title: tr("Everything in one place", "Всё в одном месте"),
      text: tr("Analytics across every connected advertising platform.", "Аналитика по всем подключённым рекламным площадкам."),
    },
    {
      icon: "verified_user",
      title: tr("Secure access", "Безопасный доступ"),
      text: tr("Phone verification protects your account from the first sign-in.", "Подтверждение телефона защищает аккаунт с первого входа."),
    },
    {
      icon: "architecture",
      title: tr("Ready to scale", "Готово к росту"),
      text: tr("Add accounts, teams and new data sources as you grow.", "Добавляйте кабинеты, команды и новые источники данных."),
    },
  ], [locale]);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
  }

  return (
    <div className={styles.page} data-i18n-skip>
      <aside className={styles.hero}>
        <div className={styles.pattern} />
        <div className={styles.heroInner}>
          <Link className={styles.brand} href="/login">Envidicy</Link>
          <h1 className={styles.heroTitle}>{tr("One dashboard. Every result.", "Один кабинет. Все результаты.")}</h1>
          <p className={styles.heroSubtitle}>
            {tr(
              "Create your workspace and keep advertising analytics, finances and access under control.",
              "Создайте рабочее пространство и держите аналитику, финансы и доступы под контролем.",
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
          <span>{tr("Secure onboarding", "Безопасная регистрация")}</span>
          <div className={styles.heroLine} />
        </div>
      </aside>

      <main className={styles.side}>
        <div className={`${styles.panel} ${styles.registerPanel}`}>
          <nav className={styles.products} aria-label="Envidicy products">
            <a href="https://app.envidicy.kz">App Envidicy</a>
            <a className={styles.productActive} href="https://dash.envidicy.kz">Dash Envidicy</a>
            <a href="https://crm.envidicy.kz">CRM Envidicy</a>
          </nav>
          <span className={styles.mobileBrand}>Envidicy</span>
          <div className={styles.panelHead}>
            <h2 className={styles.panelTitle}>{tr("Create account", "Создать аккаунт")}</h2>
            <p className={styles.panelText}>
              {tr("Enter your details and verify your WhatsApp number.", "Заполните данные и подтвердите номер через WhatsApp.")}
            </p>
          </div>

          <div className={styles.card}>
            <form className={`${styles.form} ${styles.registerForm}`} onSubmit={onSubmit}>
              <div className={styles.fieldGrid}>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>{tr("Name", "Имя")}</span>
                  <input className={styles.fieldInput} name="name" type="text" placeholder={tr("Anna Marketer", "Анна Маркетолог")} required />
                </label>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>{tr("Company", "Компания")}</span>
                  <input className={styles.fieldInput} name="company" type="text" placeholder="ACME Corp" />
                </label>
              </div>

              <label className={styles.field}>
                <span className={styles.fieldLabel}>{tr("Phone number", "Номер телефона")}</span>
                <div className={styles.codeRow}>
                  <input className={styles.fieldInput} name="phone" type="tel" inputMode="tel" autoComplete="tel" placeholder="+7 700 000 00 00" required />
                  <button className={styles.codeButton} type="button">{tr("Send code", "Отправить код")}</button>
                </div>
              </label>

              <label className={styles.field}>
                <span className={styles.fieldLabel}>Email</span>
                <input className={styles.fieldInput} name="email" type="email" placeholder="name@company.com" required />
              </label>

              <div className={styles.fieldGrid}>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>{tr("Password", "Пароль")}</span>
                  <input className={styles.fieldInput} name="password" type="password" placeholder="********" required />
                </label>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>{tr("Confirm password", "Повторите пароль")}</span>
                  <input className={styles.fieldInput} name="confirm_password" type="password" placeholder="********" required />
                </label>
              </div>

              <button className={styles.submit} type="submit" disabled>
                <span>{tr("Create account", "Создать аккаунт")}</span>
                <ArrowIcon />
              </button>
              <div className={styles.divider}><span>{tr("or", "или")}</span></div>
              <button className={styles.metaButton} type="button">
                <span className={styles.metaMark}>f</span>
                <span>{tr("Continue with Meta", "Продолжить через Meta")}</span>
              </button>
            </form>
          </div>

          <div className={styles.bottomLinks}>
            <Link href="/login">
              <span>{tr("Already have an account", "Уже есть аккаунт")}</span>
              <ChevronIcon />
            </Link>
          </div>
          <p className={styles.status}>{status}</p>
        </div>
      </main>
    </div>
  );
}
