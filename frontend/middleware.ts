import { NextRequest, NextResponse } from "next/server";
import { documentEntryDecision } from "./lib/documentEntry";

const LEGACY_PRODUCTION_HOST = "client-dash-up.vercel.app";
const CANONICAL_PRODUCTION_HOST = "dash.envidicy.kz";

export async function middleware(request: NextRequest) {
  if (request.nextUrl.hostname.toLowerCase() === LEGACY_PRODUCTION_HOST) {
    const canonical = request.nextUrl.clone();
    canonical.protocol = "https:";
    canonical.hostname = CANONICAL_PRODUCTION_HOST;
    canonical.port = "";
    return NextResponse.redirect(canonical, 308);
  }
  const decision = await documentEntryDecision(request, process.env.API_UPSTREAM_BASE || "http://127.0.0.1:8000");
  if (decision.kind === "continue") return NextResponse.next();
  const headers = { "Cache-Control": "private, no-store", "Vary": "Cookie, Authorization, X-Session-Token" };
  if (decision.kind === "redirect") {
    return NextResponse.redirect(new URL(decision.location, request.url), { status: 303, headers });
  }
  return new NextResponse("Не удалось проверить вход в Dash. Обновите страницу, чтобы повторить проверку.", {
    status: 503, headers: { ...headers, "Content-Type": "text/plain; charset=utf-8", "Retry-After": "5" },
  });
}

export const config = {
  matcher: "/:path*",
};
