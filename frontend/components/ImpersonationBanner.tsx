"use client";

import { useEffect, useState } from "react";

const LS_API_BASE = "ops_api_base";
const LS_SESSION_TOKEN = "ops_session_token";
const SESSION_UPDATED_EVENT = "ops-session-updated";

const IMPERSONATION_LABEL = "ops_impersonation_label";
const IMPERSONATION_RETURN_API_BASE = "ops_impersonation_return_api_base";
const IMPERSONATION_RETURN_TOKEN = "ops_impersonation_return_token";

export function ImpersonationBanner() {
  const [label, setLabel] = useState("");

  useEffect(() => {
    function refresh() {
      setLabel(localStorage.getItem(IMPERSONATION_LABEL) || "");
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
    const token = localStorage.getItem(IMPERSONATION_RETURN_TOKEN) || "";
    const apiBase = localStorage.getItem(IMPERSONATION_RETURN_API_BASE) || "";
    if (apiBase) localStorage.setItem(LS_API_BASE, apiBase);
    if (token) localStorage.setItem(LS_SESSION_TOKEN, token);
    localStorage.removeItem(IMPERSONATION_LABEL);
    localStorage.removeItem(IMPERSONATION_RETURN_API_BASE);
    localStorage.removeItem(IMPERSONATION_RETURN_TOKEN);
    window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
    window.location.replace("/platform/agencies");
  }

  return (
    <div className="impersonation-banner">
      <div>
        <strong>Impersonation mode</strong>
        <span>You are working as {label}</span>
      </div>
      <button className="ghost-btn" onClick={returnToAdmin}>
        Return to admin
      </button>
    </div>
  );
}
