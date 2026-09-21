"use client";

import Link from "next/link";
import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "../../../hooks/useAuth";
import { destinationForRole } from "../../../lib/authRedirect";
import { clearSessionToken } from "../../../lib/sessionToken";
import { canManageEnvidicyBudgets, envidicyLoginPath, envidicyLoginPolicy, safeEnvidicyReturnPath, shouldRedirectToMy, ENVIDICY_MY_URL, type EnvidicyLoginPolicy } from "../../../lib/envidicyAuth";

function LoginSuccessPageContent() {
  const router = useRouter();
  const search = useSearchParams();
  const { ready, authenticated, role, me, error, status, refresh } = useAuth(process.env.NEXT_PUBLIC_API_BASE || "/api/backend");
  const [checking, setChecking] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [policy, setPolicy] = useState<EnvidicyLoginPolicy | null>(null);
  const [policyPending, setPolicyPending] = useState(true);
  const completed = useRef(false);
  const next = safeEnvidicyReturnPath(search.get("next"));

  useEffect(() => {
    let active = true;
    setChecking(true);
    void refresh().finally(() => { if (active) setChecking(false); });
    return () => { active = false; };
  }, [attempt, refresh]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setPolicy(null);
    setPolicyPending(true);
    const timer = window.setTimeout(() => { controller.abort(); if (active) setPolicyPending(false); }, 10_000);
    void fetch("/api/backend/auth/envidicy/config", { cache: "no-store", credentials: "include", signal: controller.signal })
      .then(async (response) => {
        const body: unknown = response.status === 200 ? await response.json() : null;
        if (active && !controller.signal.aborted) setPolicy(envidicyLoginPolicy(body, response.status));
      }).catch(() => { /* Unknown policy does not authorize legacy login. */ })
      .finally(() => { window.clearTimeout(timer); if (active) setPolicyPending(false); });
    return () => { active = false; window.clearTimeout(timer); controller.abort(); };
  }, [attempt]);

  useEffect(() => {
    if (checking || !ready || !authenticated || error || !role || completed.current) return;
    if (me?.session?.auth_source !== "envidicy_id" && policy?.localAuthEnabled !== true) return;
    completed.current = true;
    if (shouldRedirectToMy(me?.session)) {
      window.location.replace(ENVIDICY_MY_URL);
      return;
    }
    const manageBudget = canManageEnvidicyBudgets(me?.session) && new URL(next, window.location.origin).pathname === "/budgets";
    window.dispatchEvent(new Event("ops-session-updated"));
    router.replace(manageBudget ? next : destinationForRole(role, next));
  }, [authenticated, checking, error, me, next, policy, ready, role, router]);

  const pending = checking || !ready || (authenticated && !error && policyPending);
  return (
    <main className="login-shell">
      <section className="login-card" aria-live="polite">
        <h1>{pending ? "Завершаем вход" : "Не удалось завершить вход"}</h1>
        <p className="panel-subtitle">
          {pending ? "Проверяем сессию и открываем нужный раздел."
            : status === 401 ? "Браузер не передал действующую сессию. Разрешите cookies для Dash и повторите вход вручную."
              : "Не удалось проверить сессию. Повторите проверку — автоматический вход приостановлен."}
        </p>
        {!pending && <button className="primary-btn" type="button" onClick={() => setAttempt((value) => value + 1)}>Повторить проверку сессии</button>}
        {!pending && policy?.idEnabled && <a className="primary-btn" href={envidicyLoginPath(next)} onClick={() => clearSessionToken()}>Войти через Envidicy ID</a>}
        {!pending && policy?.localAuthEnabled && <Link href={`/login?${new URLSearchParams({ legacy: "1", next })}`}>Использовать прежний вход в Dash</Link>}
        {!pending && !policyPending && !policy && <p role="status">Сервис входа временно недоступен. Повторите проверку позже.</p>}
      </section>
    </main>
  );
}

export default function LoginSuccessPage() {
  return (
    <Suspense fallback={<main className="login-shell" />}>
      <LoginSuccessPageContent />
    </Suspense>
  );
}
