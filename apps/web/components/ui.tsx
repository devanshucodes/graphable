"use client";
import Link from "next/link";
import { useEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from "react";

/* ---------- page chrome ---------- */

type Crumb = string | { label: string; href?: string; onClick?: () => void };
export function PageBar({ crumbs, actions, children }: { crumbs: Crumb[]; actions?: ReactNode; children?: ReactNode }) {
  return (
    <div className="sticky top-0 z-20 flex h-12 shrink-0 items-center justify-between gap-3 border-b border-line bg-panel/90 px-5 backdrop-blur">
      <div className="flex min-w-0 items-center gap-1.5 text-[13px]">
        {crumbs.map((c, i) => {
          const last = i === crumbs.length - 1, o = typeof c === "string" ? { label: c } : c;
          const cls = last ? "truncate font-medium text-ink" : "truncate text-ink-2 hover:text-ink";
          return (
            <span key={i} className="flex min-w-0 items-center gap-1.5">
              {o.href ? <Link href={o.href} className={cls}>{o.label}</Link> : o.onClick ? <button onClick={o.onClick} className={cls}>{o.label}</button> : <span className={cls}>{o.label}</span>}
              {!last && <span className="text-ink-3">/</span>}
            </span>
          );
        })}
        {children}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Section({ title, count, right, children, className = "" }: { title: ReactNode; count?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={className}>
      <div className="mb-2.5 flex min-h-6 items-center justify-between gap-3">
        <h3 className="text-[13px] font-semibold text-ink">{title}{count != null && <span className="ml-1.5 font-normal text-ink-3 num">{count}</span>}</h3>
        {right && <div className="flex items-center gap-2 text-[12px] text-ink-3">{right}</div>}
      </div>
      {children}
    </section>
  );
}

/* ---------- controls ---------- */

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost"; size?: "sm" | "md"; icon?: ReactNode };
export function Button({ variant = "secondary", size = "sm", icon, children, className = "", ...p }: BtnProps) {
  const v = {
    primary: "bg-ink text-white hover:bg-[#3a3a37] disabled:bg-[#b9b9b4]",
    secondary: "border border-line-2 bg-panel text-ink shadow-[0_1px_1px_rgba(0,0,0,0.03)] hover:bg-hover disabled:text-ink-3",
    ghost: "text-ink-2 hover:bg-hover hover:text-ink disabled:text-ink-3",
  }[variant];
  const s = size === "sm" ? "h-7 gap-1.5 px-2.5 text-[12.5px]" : "h-8 gap-2 px-3 text-[13px]";
  return <button {...p} className={`inline-flex shrink-0 items-center justify-center rounded-md font-medium transition-colors disabled:cursor-not-allowed ${v} ${s} ${className}`}>{icon}{children}</button>;
}

export function Tabs<T extends string>({ value, onChange, tabs }: { value: T; onChange: (t: T) => void; tabs: { key: T; label: string; count?: ReactNode; disabled?: boolean }[] }) {
  return (
    <div role="tablist" className="flex items-center gap-5 border-b border-line">
      {tabs.map((t) => (
        <button key={t.key} role="tab" aria-selected={value === t.key} disabled={t.disabled} onClick={() => onChange(t.key)}
          className={`-mb-px flex h-9 items-center gap-1.5 border-b-[1.5px] text-[13px] transition-colors disabled:cursor-not-allowed disabled:text-ink-3 ${value === t.key ? "border-ink font-medium text-ink" : "border-transparent text-ink-2 hover:text-ink"}`}>
          {t.label}{t.count != null && <span className="text-[12px] font-normal text-ink-3 num">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Segmented<T extends string>({ value, onChange, options }: { value: T; onChange: (t: T) => void; options: { key: T; label: ReactNode }[] }) {
  return (
    <div role="group" className="flex rounded-md bg-hover p-0.5">
      {options.map((o) => (
        <button key={o.key} aria-pressed={value === o.key} onClick={() => onChange(o.key)}
          className={`flex h-6 items-center gap-1.5 rounded-[5px] px-2.5 text-[12.5px] ${value === o.key ? "bg-panel font-medium text-ink shadow-[0_0_0_1px_var(--line),0_1px_2px_rgba(0,0,0,0.05)]" : "text-ink-2 hover:text-ink"}`}>{o.label}</button>
      ))}
    </div>
  );
}

export function Menu({ trigger, children, width = 320 }: { trigger: (open: boolean, toggle: () => void) => ReactNode; children: (close: () => void) => ReactNode; width?: number }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", h); document.addEventListener("keydown", k);
    return () => { document.removeEventListener("mousedown", h); document.removeEventListener("keydown", k); };
  }, [open]);
  return (
    <div ref={ref} className="relative">
      {trigger(open, () => setOpen((o) => !o))}
      {open && (
        <div role="menu" className="fade-in absolute right-0 top-full z-30 mt-1.5 rounded-lg border border-line bg-panel p-1 shadow-[0_10px_30px_rgba(0,0,0,0.10),0_2px_6px_rgba(0,0,0,0.04)]" style={{ width }}>
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

export const Kbd = ({ children }: { children: ReactNode }) => <kbd className="rounded border border-line bg-panel px-1 font-sans text-[10.5px] font-medium text-ink-3">{children}</kbd>;

/* ---------- data display ---------- */

export function Tag({ children, tone = "gray", className = "", title }: { children: ReactNode; tone?: string; className?: string; title?: string }) {
  return <span title={title} className={`inline-flex h-5 shrink-0 items-center gap-1 whitespace-nowrap rounded-[5px] border px-1.5 text-[11.5px] font-medium t-${tone} ${className}`}>{children}</span>;
}

export function Sev({ level, compact = false }: { level?: number | null; compact?: boolean }) {
  const l = level ?? 5;
  const color = l <= 1 ? "#e5484d" : l === 2 ? "#ec5d2a" : l === 3 ? "#f5a623" : "#b4b4ae";
  const filled = l <= 2 ? 3 : l === 3 ? 2 : 1;
  return (
    <span className="inline-flex items-center gap-1.5 text-[12.5px] text-ink">
      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
        {[0, 1, 2].map((i) => <rect key={i} x={i * 4.2} y={8 - i * 3.5} width="3" height={4 + i * 3.5} rx=".8" fill={i < filled ? color : "#e2e2de"} />)}
      </svg>
      {!compact && <span className="num">SEV{l}</span>}
    </span>
  );
}

const STATUS: Record<string, [string, string, boolean?]> = {
  investigating: ["#f5a623", "Investigating", true], mitigating: ["#f5a623", "Mitigating", true], awaiting_approval: ["#6e56cf", "Awaiting approval", true], closed: ["#30a46c", "Mitigated"],
  closed_rejected: ["#e5484d", "Closed, page rejected"],
  open: ["#f5a623", "Open"], resolved: ["#30a46c", "Resolved"], mitigated: ["#30a46c", "Mitigated"], executed: ["#30a46c", "Done"],
  approved: ["#30a46c", "Approved"], rejected: ["#e5484d", "Rejected"], proposed: ["#b4b4ae", "Proposed"],
};
export function Status({ s, label }: { s: string; label?: string }) {
  const [c, l, live] = STATUS[s] ?? ["#b4b4ae", s.replaceAll("_", " ")];
  return (
    <span className="inline-flex items-center gap-1.5 text-[12.5px] text-ink">
      <i className={`h-[7px] w-[7px] shrink-0 rounded-full ${live ? "pulse" : ""}`} style={{ background: c, color: c }} />{label ?? l}
    </span>
  );
}

const AV: [string, string][] = [["#e8f1ff", "#0b68cb"], ["#f1ecfe", "#5746af"], ["#e6f7ef", "#18794e"], ["#fff1e6", "#b54708"], ["#fdebf3", "#bc2d73"], ["#e5f6f7", "#0c7480"], ["#f0f0ee", "#4b4b47"]];
export function Avatar({ name, size = 20, dim = false }: { name?: string | null; size?: number; dim?: boolean }) {
  const n = name || "?";
  const initials = n.split(/\s+/).filter(Boolean).map((p) => p[0]).slice(0, 2).join("").toUpperCase();
  const [bg, fg] = dim ? ["#efefed", "#a3a39d"] : AV[[...n].reduce((a, ch) => a + ch.charCodeAt(0), 0) % AV.length];
  return <span title={n} className="inline-flex shrink-0 items-center justify-center rounded-full font-semibold" style={{ width: size, height: size, fontSize: size * 0.42, background: bg, color: fg }}>{initials}</span>;
}

export function Person({ name, sub, dim, size = 20 }: { name?: string | null; sub?: ReactNode; dim?: boolean; size?: number }) {
  if (!name) return <span className="text-ink-3">—</span>;
  return <span className={`inline-flex min-w-0 items-center gap-1.5 ${dim ? "text-ink-3" : ""}`}><Avatar name={name} size={size} dim={dim} /><span className="truncate">{name}</span>{sub && <span className="truncate text-ink-3">{sub}</span>}</span>;
}

export function Meter({ value, color = "#1c1c1a", width }: { value: number; color?: string; width?: number }) {
  return (
    <span className="relative inline-block h-1 overflow-hidden rounded-full bg-[#ececea] align-middle" style={{ width: width ?? "100%" }}>
      <span className="absolute inset-y-0 left-0 rounded-full transition-[width] duration-500" style={{ width: `${Math.max(2, Math.min(100, value * 100))}%`, background: color }} />
    </span>
  );
}

export function Props({ children }: { children: ReactNode }) { return <dl className="space-y-0.5">{children}</dl>; }
export function Prop({ k, title, children }: { k: string; title?: string; children: ReactNode }) {
  return (
    <div className="grid min-h-8 grid-cols-[104px_minmax(0,1fr)] items-center gap-2 text-[13px]">
      <dt title={title} className={`text-ink-3 ${title ? "w-fit cursor-help underline decoration-[#c9c9c4] decoration-dotted underline-offset-[3px]" : ""}`}>{k}</dt><dd className="min-w-0 text-ink">{children}</dd>
    </div>
  );
}

export function Note({ tone = "amber", icon, children }: { tone?: string; icon?: ReactNode; children: ReactNode }) {
  return <div className={`flex gap-2 rounded-md border px-2.5 py-2 text-[12.5px] leading-snug t-${tone}`}>{icon && <span className="mt-px shrink-0">{icon}</span>}<div className="min-w-0">{children}</div></div>;
}

export function Empty({ title, sub, action }: { title: string; sub?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-line-2 px-6 py-10 text-center">
      <div className="text-[13px] font-medium text-ink">{title}</div>
      {sub && <div className="max-w-sm text-[12.5px] text-ink-3">{sub}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/* ---------- shared vocab ---------- */

export const AGENT: Record<string, { label: string; color: string }> = {
  triage: { label: "Triage", color: "#0090ff" },
  investigator: { label: "Investigator", color: "#6e56cf" },
  responder: { label: "Responder", color: "#30a46c" },
  scribe: { label: "Scribe", color: "#95958f" },
};

export const TOOL_LABEL: Record<string, string> = {
  incident_blast_radius: "mapped the blast radius", org_ownership: "looked up owner and on-call", incident_similar: "searched past incidents",
  memory_note: "wrote a note to the blackboard", memory_handoff: "handed off", memory_inbox: "read its handoff from the graph", incident_rank_root_causes: "ranked root-cause suspects",
  knowledge_for_entity: "pulled runbooks and docs", graph_explain_connection: "traced a connection", graph_neighbors: "explored neighbours",
  graph_cypher_readonly: "ran a read-only Cypher query", org_experts: "found who knows it", memory_recall: "recalled lessons",
  action_create_ticket: "filed a ticket", action_page_oncall: "requested a page", action_propose_remediation: "proposed a remediation",
  graph_resolve: "resolved an entity", knowledge_search: "searched knowledge", decision_trace: "traced a decision", graph_hubs: "ranked the alerting services with PageRank",
};
