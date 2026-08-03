"use client";

import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { safeRelativePath } from "../../../lib/authRedirect";

const SESSION_UPDATED_EVENT = "ops-session-updated";

export default function LoginSuccessPage() {
  const router = useRouter();
  const search = useSearchParams();

  useEffect(() => {
    const next = safeRelativePath(search.get("next"), "/");
    window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
    router.replace(next);
  }, [router, search]);

  return (
    <main className="login-shell">
      <section className="login-card">
        <h1>Завершаем вход</h1>
        <p className="panel-subtitle">Проверяем OAuth-сессию и открываем нужный раздел.</p>
      </section>
    </main>
  );
}
