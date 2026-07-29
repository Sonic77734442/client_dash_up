import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "content-encoding",
  "content-length",
  "host",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

function upstreamBase(): URL {
  const raw = (process.env.API_UPSTREAM_BASE || "http://127.0.0.1:8000").trim();
  const parsed = new URL(raw);
  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new Error("API_UPSTREAM_BASE must use http or https");
  }
  parsed.pathname = parsed.pathname.replace(/\/+$/, "") + "/";
  parsed.search = "";
  parsed.hash = "";
  return parsed;
}

function targetUrl(request: NextRequest, path: string[]): URL {
  const base = upstreamBase();
  const encodedPath = path.map((segment) => encodeURIComponent(segment)).join("/");
  const target = new URL(encodedPath, base);
  target.search = request.nextUrl.search;
  if (target.origin !== base.origin) {
    throw new Error("Resolved API target escaped configured upstream");
  }
  return target;
}

function requestHeaders(request: NextRequest): Headers {
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase()) && !key.toLowerCase().startsWith("x-forwarded-")) {
      headers.append(key, value);
    }
  });
  headers.set("x-forwarded-host", request.nextUrl.host);
  headers.set("x-forwarded-proto", request.nextUrl.protocol.replace(":", ""));
  return headers;
}

function responseHeaders(upstream: Response): Headers {
  const headers = new Headers();
  upstream.headers.forEach((value, key) => {
    const normalized = key.toLowerCase();
    if (!HOP_BY_HOP_HEADERS.has(normalized) && normalized !== "set-cookie") {
      headers.append(key, value);
    }
  });

  const cookieHeaders = upstream.headers as Headers & { getSetCookie?: () => string[] };
  const cookies = cookieHeaders.getSetCookie?.() || [];
  if (cookies.length) {
    for (const cookie of cookies) headers.append("set-cookie", cookie);
  } else {
    const cookie = upstream.headers.get("set-cookie");
    if (cookie) headers.append("set-cookie", cookie);
  }
  return headers;
}

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  try {
    const { path } = await context.params;
    const method = request.method.toUpperCase();
    const body = method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();
    const upstream = await fetch(targetUrl(request, path || []), {
      method,
      headers: requestHeaders(request),
      body,
      redirect: "manual",
      cache: "no-store",
    });

    return new NextResponse(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders(upstream),
    });
  } catch (error) {
    console.error("API proxy failed", error instanceof Error ? error.message : "unknown error");
    return NextResponse.json(
      {
        error: {
          code: "api_proxy_unavailable",
          message: "API is temporarily unavailable",
          details: {},
        },
      },
      { status: 502 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;
