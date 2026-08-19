import { NextRequest, NextResponse } from "next/server";

const LEGACY_PRODUCTION_HOST = "client-dash-up.vercel.app";
const CANONICAL_PRODUCTION_HOST = "dash.envidicy.kz";

export function middleware(request: NextRequest) {
  if (request.nextUrl.hostname.toLowerCase() !== LEGACY_PRODUCTION_HOST) {
    return NextResponse.next();
  }

  const canonical = request.nextUrl.clone();
  canonical.protocol = "https:";
  canonical.hostname = CANONICAL_PRODUCTION_HOST;
  canonical.port = "";
  return NextResponse.redirect(canonical, 308);
}

export const config = {
  matcher: "/:path*",
};
