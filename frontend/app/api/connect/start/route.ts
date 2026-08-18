import { NextRequest, NextResponse } from "next/server";
import {
  OAuthRelayRequestError,
  resolveOAuthRelayTarget,
} from "../../../../lib/oauthLaunchRelay";

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

function relayRequestHeaders(request: NextRequest): Headers {
  const headers = new Headers();
  const cookie = request.headers.get("cookie");
  const authorization = request.headers.get("authorization");
  const sessionToken = request.headers.get("x-session-token");
  const accept = request.headers.get("accept");

  if (cookie) headers.set("cookie", cookie);
  if (authorization) headers.set("authorization", authorization);
  if (sessionToken) headers.set("x-session-token", sessionToken);
  if (accept) headers.set("accept", accept);
  headers.set("x-forwarded-host", request.nextUrl.host);
  headers.set("x-forwarded-proto", request.nextUrl.protocol.replace(":", ""));
  return headers;
}

function relayResponseHeaders(upstream: Response): Headers {
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

export async function GET(request: NextRequest): Promise<NextResponse> {
  try {
    const target = resolveOAuthRelayTarget(request.nextUrl, process.env.API_UPSTREAM_BASE);
    const upstream = await fetch(target, {
      method: "GET",
      headers: relayRequestHeaders(request),
      redirect: "manual",
      cache: "no-store",
    });

    return new NextResponse(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: relayResponseHeaders(upstream),
    });
  } catch (error) {
    if (error instanceof OAuthRelayRequestError) {
      return NextResponse.json(
        { error: { code: error.code, message: error.message, details: {} } },
        { status: error.status },
      );
    }

    // Do not log the target URL: OAuth query strings may contain tenant identifiers.
    console.error("OAuth launch relay failed");
    return NextResponse.json(
      {
        error: {
          code: "oauth_relay_unavailable",
          message: "OAuth is temporarily unavailable",
          details: {},
        },
      },
      { status: 502 },
    );
  }
}
