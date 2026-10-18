"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { BarChart3, BookOpen, Boxes, Inbox, MessageSquareText, Search, Siren } from "lucide-react";
import { get } from "@/lib/api";

const GROUPS = [
  { items: [{ href: "/", label: "Incidents", icon: Siren }, { href: "/brain", label: "Ask the Brain", icon: MessageSquareText }] },
  { title: "Company graph", items: [{ href: "/explorer", label: "Services", icon: Boxes }, { href: "/ingest", label: "Live ingest", icon: Inbox }] },
  { title: "Agents", items: [{ href: "/memory", label: "Memory", icon: BookOpen }, { href: "/scoreboard", label: "Evaluation", icon: BarChart3 }] },
];
const isActive = (path: string, href: string) => (href === "/" ? path === "/" : path.startsWith(href));
const fmtK = (n?: number) => (n == null ? "—" : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
const ACTIVE = "bg-panel font-medium text-ink shadow-[0_0_0_1px_var(--line),0_1px_2px_rgba(0,0,0,0.04)]";

export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <rect width="24" height="24" rx="6" fill="#1c1c1a" />
      <path d="M7.5 8.5L16 7.2M7.5 8.5L11.5 16.5M16 7.2L11.5 16.5" stroke="#fff" strokeOpacity=".55" strokeWidth="1.3" />
      <circle cx="7.5" cy="8.5" r="2.1" fill="#fff" /><circle cx="16" cy="7.2" r="2.1" fill="#fff" /><circle cx="11.5" cy="16.5" r="2.4" fill="#a594f9" />
    </svg>
  );
}

export default function Sidebar() {
  const path = usePathname();
  const router = useRouter();
  const [h, setH] = useState<any>(null);
  const [ov, setOv] = useState<any>(null);
  const [db, setDb] = useState<boolean | null>(null); // did the last graph query (/overview) succeed?
  // refetch on every route change so the counts follow what the other pages just wrote to the graph
  useEffect(() => {
    let live = true;
    get("/health").then((x) => { if (live) setH(x); }).catch(() => { if (live) setH({ ok: false }); });
    get("/overview").then((x) => { if (live) { setOv(x); setDb(true); } }).catch(() => { if (live) setDb(false); });
    return () => { live = false; };
  }, [path]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); router.push("/brain"); } };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [router]);
  const dbDot = db === false || h?.ok === false ? "bg-red" : db ? "bg-green" : "bg-[#c9c9c4]";

  return (
    <>
      {/* small screens: logo and a horizontally scrolling nav above the page */}
      <header className="shrink-0 border-b border-line md:hidden">
        <div className="flex h-11 items-center gap-2 px-4">
          <Logo size={20} />
          <span className="text-[14px] font-semibold tracking-[-0.01em]">Graphable</span>
          <span className="ml-auto flex items-center gap-1.5 text-[12px] text-ink-3 num"><i className={`h-1.5 w-1.5 rounded-full ${dbDot}`} />{fmtK(ov?.nodes)} nodes</span>
        </div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-3 pb-2 [scrollbar-width:none]">
          {GROUPS.flatMap((g) => g.items).map(({ href, label, icon: Icon }) => {
            const active = isActive(path, href);
            return (
              <Link key={href} href={href} aria-current={active ? "page" : undefined}
                className={`flex h-7 shrink-0 items-center gap-1.5 rounded-md px-2 text-[12.5px] ${active ? ACTIVE : "text-ink-2 hover:bg-[#e8e8e5] hover:text-ink"}`}>
                <Icon size={14} strokeWidth={1.8} className={active ? "text-ink" : "text-ink-3"} />{label}
              </Link>
            );
          })}
        </nav>
      </header>

      <aside className="hidden w-[232px] shrink-0 flex-col px-2.5 py-3 md:flex">
        <div className="flex items-center gap-2 px-1.5 pb-3">
          <Logo />
          <span className="text-[14px] font-semibold tracking-[-0.01em]">Graphable</span>
        </div>
        <div className="mb-2 flex h-8 items-center gap-2 px-1.5">
          <span className="flex h-5 w-5 items-center justify-center rounded-[5px] bg-[#e5484d] text-[10.5px] font-semibold text-white">A</span>
          <span className="flex-1 truncate text-[13px] font-medium">Acme Commerce</span>
        </div>
        <Link href="/brain" className="mb-4 flex h-8 items-center gap-2 rounded-md border border-line bg-panel px-2 text-[13px] text-ink-3 shadow-[0_1px_1px_rgba(0,0,0,0.03)] hover:border-line-2">
          <Search size={14} /> <span className="flex-1">Ask anything</span>
          <kbd className="rounded border border-line px-1 text-[10.5px] font-medium text-ink-3">⌘K</kbd>
        </Link>
        <nav aria-label="Main" className="flex-1 space-y-4">
          {GROUPS.map((g, i) => (
            <div key={i}>
              {g.title && <div className="px-2 pb-1 text-[11.5px] font-medium text-ink-3">{g.title}</div>}
              <div className="space-y-px">
                {g.items.map(({ href, label, icon: Icon }) => {
                  const active = isActive(path, href);
                  return (
                    <Link key={href} href={href} aria-current={active ? "page" : undefined}
                      className={`flex h-[30px] items-center gap-2.5 rounded-md px-2 text-[13px] ${active ? ACTIVE : "text-ink-2 hover:bg-[#e8e8e5] hover:text-ink"}`}>
                      <Icon size={15} strokeWidth={1.8} className={active ? "text-ink" : "text-ink-3"} />{label}
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>
        <div className="space-y-1.5 px-2 pt-3 text-[12px] text-ink-2">
          <div>
            <div className="flex items-center gap-2"><i className={`h-1.5 w-1.5 rounded-full ${dbDot}`} />FalkorDB<span className="ml-auto text-ink-3 num">{fmtK(ov?.nodes)} nodes</span></div>
            <div className="text-right text-ink-3 num">{fmtK(ov?.edges)} edges</div>
          </div>
          <div className="flex items-center gap-2"><i className={`h-1.5 w-1.5 rounded-full ${h?.llm ? "bg-green" : "bg-[#c9c9c4]"}`} />Gemini<span className="ml-auto text-ink-3">{h == null ? "—" : h.llm ? "on" : "offline"}</span></div>
        </div>
      </aside>
    </>
  );
}
