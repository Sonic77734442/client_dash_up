"use client";

import { useState } from "react";
import { ENVIDICY_MY_URL } from "../lib/envidicyAuth";

export function EnvidicyAccessGate({ issue, refresh, logout }: {
  issue: "not_granted" | "project_unlinked" | "context_unavailable";
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const unavailable = issue === "context_unavailable";
  return (
    <main className="auth-outage-shell" role="status" aria-live="polite">
      <section className="auth-outage-card">
        <div className="auth-outage-eyebrow">Envidicy ID · Dash</div>
        <h1>{unavailable ? "Не удалось проверить доступ в My" : "Доступ к Dash настраивается в My"}</h1>
        <p>
          {unavailable
            ? "Вход через Envidicy ID выполнен. My временно недоступен — повторите проверку позже. Повторно входить не нужно."
            : issue === "project_unlinked"
              ? "Вход выполнен, но проект My ещё не связан с клиентским кабинетом Dash. Обратитесь к администратору проекта."
              : "Вход через Envidicy ID выполнен. Откройте My, выберите проект и запросите доступ к Dash у администратора."}
        </p>
        <a className="primary-btn" href={ENVIDICY_MY_URL}>Открыть My</a>
        <div style={{ display: "flex", gap: 12, justifyContent: "center", marginTop: 16, flexWrap: "wrap" }}>
          <button className="ghost-btn" type="button" disabled={busy} onClick={() => {
            setBusy(true);
            void refresh().finally(() => setBusy(false));
          }}>{busy ? "Проверяем…" : "Повторить проверку доступа"}</button>
          <button className="ghost-btn" type="button" disabled={busy} onClick={() => {
            setBusy(true);
            setLogoutError("");
            void logout().catch(() => {
              setLogoutError("Не удалось подтвердить выход из Dash. Повторите попытку.");
            }).finally(() => setBusy(false));
          }}>Выйти из Dash</button>
        </div>
        {logoutError && <p role="alert">{logoutError}</p>}
      </section>
    </main>
  );
}
