export const API = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000/v1";

/** An HTTP error from the API. `message` is the server's `detail` text when it sent one. */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

async function fail(r: Response): Promise<never> {
  const text = await r.text().catch(() => "");
  let msg = text;
  try {
    const d = JSON.parse(text)?.detail;
    if (typeof d === "string") msg = d;
    else if (Array.isArray(d)) msg = d.map((x: any) => x?.msg ?? JSON.stringify(x)).join("; ");
    else if (d != null) msg = JSON.stringify(d);
  } catch { /* not JSON: keep the raw text */ }
  throw new ApiError(r.status, msg || `${r.status} ${r.statusText}`);
}

export async function get<T = any>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!r.ok) await fail(r);
  return r.json();
}

export async function post<T = any>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) await fail(r);
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

/** Graph ids to display names: svc_orders -> orders, ds_pg-orders -> pg-orders. */
export const nameOf = (id?: string | null) => (id ?? "").replace(/^(svc_|ds_)/, "");
/** Like nameOf, and also strips team_ (team_platform -> platform). */
export const short = (id?: string | null) => (id ?? "").replace(/^(svc_|ds_|team_)/, "");
/** Incident id to its key, the rule the API uses: inc_h2_99fb -> INC-H2-99FB, inc_0397 -> INC-0397. */
export const incKey = (id: string) => id.replace(/^inc_/, "INC-").toUpperCase().replaceAll("_", "-");
/** Agent prose for display: component ids to names, incident ids to keys, ASCII arrows to →. */
export const pretty = (s?: string | null) =>
  arrow((s ?? "").replace(/\b(svc|ds)_([a-z0-9-]+)/g, "$2").replace(/\binc_[a-z0-9]+(?:_[a-z0-9]+)*\b/g, incKey));

/** Fault codes the investigator assigns to non-change root causes. */
export const FAULT: Record<string, string> = {
  cert_expiry: "certificate expiry", external_outage: "third-party outage", node_failure: "node failure",
  data_issue: "data issue", capacity: "capacity saturation", unknown: "unexplained failure",
};
export const fault = (f?: string | null) => FAULT[f ?? "unknown"] ?? String(f).replaceAll("_", " ");
const FAULT_CODE = new RegExp(`\\b(${Object.keys(FAULT).filter((k) => k.includes("_")).join("|")})\\b`, "g");
/** pretty() plus tidying of developer text: Python lists, raw fault codes, threshold notation. */
export const clean = (s?: string | null) =>
  pretty((s ?? "")
    .replace(/\[\s*((?:'[^']*'|"[^"]*")(?:\s*,\s*(?:'[^']*'|"[^"]*"))*)?\s*\]/g, (_, xs?: string) => (xs ? xs.replace(/['"]/g, "") : "none"))
    .replace(/\(best change score n\/a < ([\d.]+)\)/g, "(no change in the window)")
    .replace(/\(best change score ([\d.]+) < ([\d.]+)\)/g, "(best change scored $1, below the $2 threshold)")
    .replace(/\((unknown|capacity)\)/g, (_, f: string) => `(${FAULT[f]})`)
    .replace(FAULT_CODE, (f) => FAULT[f]));

export const TYPE_COLOR: Record<string, string> = {
  Component: "#5b5bd6", Service: "#5b5bd6", Datastore: "#12a594", Change: "#d6409f", Incident: "#f76b15",
  Person: "#30a46c", Team: "#8e4ec6", Runbook: "#e2a336", Decision: "#e54d2e", Doc: "#8b8d98", Alert: "#f76b15",
};
export const ROOT_COLOR = "#e5484d";
export const ALERT_COLOR = "#f5a623";
