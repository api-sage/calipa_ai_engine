import { NextResponse, type NextRequest } from "next/server";
import { DEFAULT_USER, USER_COOKIE } from "@/lib/users";

// The browser talks only to /api/*; this forwards it to the Calipa API and attaches
// who is calling. Today that is the development header, picked with the user switch.
// When SSO lands, this is the one place that changes: read the session here instead.
const API_URL = process.env.CALIPA_API_URL ?? "http://localhost:8765";

export function middleware(request: NextRequest) {
  const path = request.nextUrl.pathname.replace(/^\/api/, "") || "/";
  const target = new URL(path + request.nextUrl.search, API_URL);

  const headers = new Headers(request.headers);
  headers.set("X-Calipa-User", request.cookies.get(USER_COOKIE)?.value ?? DEFAULT_USER);
  headers.delete("cookie"); // the API has no use for browser cookies

  return NextResponse.rewrite(target, { request: { headers } });
}

export const config = { matcher: "/api/:path*" };
