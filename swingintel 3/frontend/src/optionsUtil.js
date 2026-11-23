// Shared helpers for the Options Desk tab.

export const DIR_COLOR = { call: "#4ADE80", put: "#F87171", no_trade: "#8B93A7" };
export const DIR_LABEL = { call: "CALL", put: "PUT", no_trade: "NO TRADE" };

export const usd = (n, d = 0) =>
  n == null ? "–" : (n < 0 ? "-" : "") + "$" + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

export const num = (n, d = 2) => (n == null ? "–" : Number(n).toFixed(d));

export function fmtAge(s) {
  if (s == null) return "unknown";
  if (s < 90) return `${Math.round(s)}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  if (s < 172800) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

export function ageFrom(iso) {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : Math.max(0, (Date.now() - t) / 1000);
}

export function shortDate(expiry) {
  if (!expiry) return "";
  return new Date(expiry + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function shortContract(c) {
  if (!c || !c.found) return "no liquid contract";
  return `$${c.strike} ${c.right === "call" ? "C" : "P"} ${shortDate(c.expiry)}`;
}

export async function api(base, path, opts = {}) {
  const { timeout = 45000, ...init } = opts;
  const res = await fetch(`${base}${path}`, {
    ...init,
    headers: init.body ? { "Content-Type": "application/json", ...(init.headers || {}) } : init.headers,
    signal: AbortSignal.timeout(timeout),
  });
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON error body */ }
  if (!res.ok) throw new Error((body && body.detail) || `Request failed (${res.status})`);
  return body;
}
