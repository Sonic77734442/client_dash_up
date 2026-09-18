import { NextRequest, NextResponse } from "next/server";
import { envidicyLoginPath } from "../../../lib/envidicyAuth";

export const dynamic = "force-dynamic";

/** My launches a normal authorization-code login, never a URL-carried session. */
export function GET(request: NextRequest) {
  const target = envidicyLoginPath(request.nextUrl.searchParams.get("next"));
  return new NextResponse(null, {
    status: 303,
    headers: { Location: target, "Cache-Control": "no-store" },
  });
}
