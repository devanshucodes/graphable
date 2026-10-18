export const API = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000/v1";

export async function get<T = any>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export async function post<T = any>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

/** The demo world's "now" (simulation clock), matches DEMO_NOW in the API. */
export const DEMO_NOW = 1791936000000;

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const fmtDate = (ms?: number | null) => {
  if (!ms) return "—";
  const d = new Date(ms);
  return `${d.getUTCDate()} ${MON[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
};
export const fmtTime = (ms?: number | null) => {
  if (!ms) return "—";
  const d = new Date(ms);
  return `${d.getUTCDate()} ${MON[d.getUTCMonth()]}, ${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")} UTC`;
};
export const ago = (ms?: number | null, now = DEMO_NOW) => {
  if (!ms) return "—";
  const s = Math.max(0, (now - ms) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 86400 * 45) return `${Math.round(s / 86400)}d ago`;
  return `${Math.round(s / (86400 * 30))}mo ago`;
};
export const sentence = (s?: string | null) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");
export const arrow = (s?: string | null) => (s ? s.replaceAll(" -> ", " → ").replaceAll("->", "→") : "");

export const TYPE_COLOR: Record<string, string> = {
  Component: "#5b5bd6", Service: "#5b5bd6", Datastore: "#12a594", Change: "#d6409f", Incident: "#f76b15",
  Person: "#30a46c", Team: "#8e4ec6", Runbook: "#e2a336", Decision: "#e54d2e", Doc: "#8b8d98", Alert: "#f76b15",
};
export const ROOT_COLOR = "#e5484d";
export const ALERT_COLOR = "#f5a623";
