"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  consumeImpersonationReturnSession,
  getImpersonationLabel,
  setSessionToken,
} from "../lib/sessionToken";

const LS_API_BASE = "ops_api_base";
const SESSION_UPDATED_EVENT = "ops-session-updated";

export function ImpersonationBanner() {
  const router = useRouter();
  const [label, setLabel] = useState("");

  useEffect(() => {
    function refresh() {
      setLabel(getImpersonationLabel());
    }
    refresh();
    window.addEventListener("storage", refresh);
    window.addEventListener(SESSION_UPDATED_EVENT, refresh);
    return () => {
      window.removeEventListener("storage", refresh);
      window.removeEventListener(SESSION_UPDATED_EVENT, refresh);
    };
  }, []);

  if (!label) return null;

  function returnToAdmin() {
    const { token, apiBase } = consumeImpersonationReturnSession();
    if (apiBase) localStorage.setItem(LS_API_BASE, apiBase);
    // An empty return token means the administrator is authenticated by the
    // HttpOnly cookie. Clear the impersonated bearer token in that case.
    setSessionToken(token);
    setLabel("");
    window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
    router.replace("/platform/agencies");
  }

  return (
    <div className="impersonation-banner">
      <div>
        <strong>Режим просмотра от имени пользователя</strong>
        <span>Вы работаете как {label}</span>
      </div>
      <button className="ghost-btn" onClick={returnToAdmin}>
        Вернуться в админку
      </button>
    </div>
  );
}
