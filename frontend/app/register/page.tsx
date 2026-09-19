import { redirect } from "next/navigation";
import { isPublicPath, safeRelativePath } from "../../lib/authRedirect";

// The old page was an inert mock, not a registration endpoint. Login owns the
// current backend policy, so this alias must not fetch or invent another one.
export default async function RegisterPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string | string[] }>;
}) {
  const params = await searchParams;
  const requested = typeof params.next === "string" ? params.next : undefined;
  let next = safeRelativePath(requested, "/");
  if (isPublicPath(new URL(next, "https://dash.envidicy.local").pathname)) next = "/";
  redirect(next === "/" ? "/login" : `/login?${new URLSearchParams({ next })}`);
}
