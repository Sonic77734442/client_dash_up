export type OAuthRelayProvider = "google" | "facebook";
export type OAuthRelaySource = "g" | "m";

const SOURCE_TO_PROVIDER: Readonly<Record<OAuthRelaySource, OAuthRelayProvider>> = {
  g: "google",
  m: "facebook",
};

const PROVIDER_TO_SOURCE: Readonly<Record<OAuthRelayProvider, OAuthRelaySource>> = {
  google: "g",
  facebook: "m",
};

const FORWARDED_QUERY_KEYS = [
  "next",
  "intent",
  "connect_mode",
  "connection_key",
  "agency_id",
  "client_id",
] as const;

const SAFE_URL_ORIGIN = "https://oauth-relay.invalid";

export class OAuthRelayRequestError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status = 400) {
    super(message);
    this.name = "OAuthRelayRequestError";
    this.code = code;
    this.status = status;
  }
}

function isSafeRelativePath(value: string): boolean {
  if (
    !value.startsWith("/")
    || value.startsWith("//")
    || value.includes("\\")
    || /[\u0000-\u001f\u007f]/.test(value)
  ) {
    return false;
  }

  try {
    return new URL(value, SAFE_URL_ORIGIN).origin === SAFE_URL_ORIGIN;
  } catch {
    return false;
  }
}

function singleQueryValue(searchParams: URLSearchParams, key: string): string | null {
  const values = searchParams.getAll(key);
  if (values.length > 1) {
    throw new OAuthRelayRequestError("invalid_oauth_launch", "OAuth launch parameters are invalid");
  }
  return values[0] ?? null;
}

function relayProvider(searchParams: URLSearchParams): OAuthRelayProvider {
  const source = singleQueryValue(searchParams, "source");
  if (source !== "g" && source !== "m") {
    throw new OAuthRelayRequestError("unsupported_oauth_source", "OAuth source is not supported");
  }
  return SOURCE_TO_PROVIDER[source];
}

function upstreamBase(rawValue?: string): URL {
  const raw = (rawValue || "http://127.0.0.1:8000").trim();
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new OAuthRelayRequestError(
      "oauth_relay_unavailable",
      "OAuth is temporarily unavailable",
      502,
    );
  }
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) {
    throw new OAuthRelayRequestError(
      "oauth_relay_unavailable",
      "OAuth is temporarily unavailable",
      502,
    );
  }
  parsed.pathname = parsed.pathname.replace(/\/+$/, "") + "/";
  parsed.search = "";
  parsed.hash = "";
  return parsed;
}

/**
 * Resolves the neutral same-origin launch URL to one fixed, allowlisted backend
 * OAuth endpoint. Unknown query fields are deliberately dropped so browser
 * input cannot select another upstream path or accidentally relay secrets.
 */
export function resolveOAuthRelayTarget(requestUrl: URL, rawUpstreamBase?: string): URL {
  const provider = relayProvider(requestUrl.searchParams);
  const base = upstreamBase(rawUpstreamBase);
  const target = new URL(`auth/${provider}/start`, base);

  for (const key of FORWARDED_QUERY_KEYS) {
    const value = singleQueryValue(requestUrl.searchParams, key);
    if (value !== null) target.searchParams.set(key, value);
  }

  const nextPath = target.searchParams.get("next");
  if (nextPath !== null && !isSafeRelativePath(nextPath)) {
    throw new OAuthRelayRequestError("invalid_oauth_next", "OAuth return path is invalid");
  }

  const intent = target.searchParams.get("intent");
  if (
    intent !== null
    && intent !== "login"
    && intent !== "connect"
    && intent !== "migrate"
    && intent !== "link"
  ) {
    throw new OAuthRelayRequestError("invalid_oauth_launch", "OAuth launch parameters are invalid");
  }

  const connectMode = target.searchParams.get("connect_mode");
  if (connectMode !== null && connectMode !== "add" && connectMode !== "overwrite") {
    throw new OAuthRelayRequestError("invalid_oauth_launch", "OAuth launch parameters are invalid");
  }

  if (target.origin !== base.origin || !target.pathname.startsWith(base.pathname)) {
    throw new OAuthRelayRequestError(
      "oauth_relay_unavailable",
      "OAuth is temporarily unavailable",
      502,
    );
  }
  return target;
}

export function oauthRelayLaunchPath(
  provider: OAuthRelayProvider,
  params?: URLSearchParams | Record<string, string>,
): string {
  const query = params instanceof URLSearchParams
    ? new URLSearchParams(params)
    : new URLSearchParams(params || {});
  query.delete("source");
  query.set("source", PROVIDER_TO_SOURCE[provider]);
  return `/api/connect/start?${query.toString()}`;
}
