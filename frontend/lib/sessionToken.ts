"use client";

let sessionToken = "";
let impersonationReturnToken = "";
let impersonationReturnApiBase = "";
let impersonationLabel = "";

export function getSessionToken(): string {
  return sessionToken;
}

export function setSessionToken(token: string): void {
  sessionToken = (token || "").trim();
}

export function clearSessionToken(): void {
  sessionToken = "";
}

export function setImpersonationReturnSession(token: string, apiBase: string, label: string): void {
  impersonationReturnToken = (token || "").trim();
  impersonationReturnApiBase = (apiBase || "").trim();
  impersonationLabel = (label || "").trim();
}

export function getImpersonationLabel(): string {
  return impersonationLabel;
}

export function consumeImpersonationReturnSession(): { token: string; apiBase: string } {
  const result = {
    token: impersonationReturnToken,
    apiBase: impersonationReturnApiBase,
  };
  impersonationReturnToken = "";
  impersonationReturnApiBase = "";
  impersonationLabel = "";
  return result;
}
