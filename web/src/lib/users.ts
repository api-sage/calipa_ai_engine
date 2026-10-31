// Development identities, from the API's entitlements file (reference/entitlements.v1.json).
// Replaced by SSO; until then the user switch picks one of these.
export const DEV_USERS = [
  { id: "auditor@bank", name: "Auditor", role: "Starts audits" },
  { id: "reviewer@bank", name: "Reviewer", role: "Reviews reports" },
  { id: "lead@bank", name: "Audit lead", role: "Starts and reviews" },
  { id: "fees@bank", name: "Fees analyst", role: "Fee controls only" },
] as const;

export const USER_COOKIE = "calipa_user";
export const DEFAULT_USER = "auditor@bank";

export function readUserCookie(): string {
  if (typeof document === "undefined") return DEFAULT_USER;
  const m = document.cookie.match(/(?:^|; )calipa_user=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : DEFAULT_USER;
}

export function writeUserCookie(user: string) {
  document.cookie = `${USER_COOKIE}=${encodeURIComponent(user)}; path=/; max-age=31536000; samesite=lax`;
}
